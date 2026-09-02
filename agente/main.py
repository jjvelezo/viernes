"""Agente: push-to-talk por voz + chat de texto.

F9 graba, Whisper transcribe, el motor LLM (Gemma 4 E2B via litert-lm-api
en GPU -- ver core/motor_llm.py) decide si responde directo o llama una
skill, y la respuesta se dice en voz alta. Ademas una ventana de chat
abajo a la derecha (core/chat_web.py, HTML/CSS en un WebView) permite
escribirle en vez de hablarle -- va al mismo motor local, con las mismas
skills.

Un solo proceso levanta tres cosas: el icono de bandeja, el push-to-talk
por voz y la ventana de chat.

Detalle de hilos (Windows): pystray y pywebview se pelean por el bucle de
mensajes del hilo principal, asi que la bandeja va con icon.run_detached()
(su propio hilo) y pywebview se queda con webview.start() en el principal.
window.evaluate_js de pywebview es thread-safe, asi que desde otros hilos
se le habla a la pagina directo (sin cola ni marshalling como con Tk).

agente/ ES Viernes y es el nucleo del repo. La carpeta hermana
mouse_gestual/ (skill de gestos, proceso aparte) NO se importa nunca aca
-- lo que se necesitaba de ahi se reescribio, no se importo -- asi agente/
puede moverse a su propio repo con git mv sin arrastrar nada."""

import queue
import random
import sys
import threading
import time

import keyboard
import pystray
import webview
from PIL import Image, ImageDraw

import config
import skills
from core import chat_web, logs, motor_llm
from core.voz import (
    EstadoGrabacion,
    barge_in_pedido,
    cargar_modelo_stt,
    detener_reproduccion,
    hablar,
    iniciar_stream,
    precargar_tts,
    reiniciar_barge_in,
    transcribir,
)

TECLA_PTT = config.obtener("push_to_talk.key", "f9")

LOG = logs.configurar()

# Referencia global al stream de audio: sin esto, el objeto sd.InputStream
# que devuelve iniciar_stream() no tiene ningun referente en Python y puede
# destruirse mientras sigue activo -- eso corrompe el heap y crashea el
# proceso con STATUS_BREAKPOINT (visto en desarrollo). Un prototipo anterior
# no tenia este problema porque creaba el stream como variable local en su
# setup(), donde quedaba referenciado.
_STREAM_AUDIO = None

# Estado del turno actual, para decidir que hacer si apretan F9 de nuevo
# antes de que el turno anterior termine:
#  - PROCESANDO (transcribiendo o pensando el LLM): se ignora el F9 -- no
#    hay audio para cortar, e interrumpir ahi arriesga tocar el modelo
#    desde dos lados a la vez.
#  - HABLANDO (reproduciendo la respuesta): "barge-in" -- se corta el
#    audio en seco (detener_reproduccion) y arranca una grabacion nueva
#    de inmediato, como cuando le hablas encima a un asistente de voz real.
ESTADO_IDLE = "idle"
ESTADO_PROCESANDO = "procesando"
ESTADO_HABLANDO = "hablando"
_estado_turno = {"valor": ESTADO_IDLE}

# Todo acceso al estado del turno pasa por este candado: lo tocan tres hilos
# (hook de teclado, hilo de procesamiento, hilo del saludo) y sin serializar
# habia una carrera fina en el barge-in (un hilo viejo reseteaba a idle
# despues de que el nuevo turno ya habia puesto "procesando").
_estado_lock = threading.Lock()

# Numero de turno. Sube cada vez que arranca uno nuevo. Un hilo de
# procesamiento viejo compara su numero contra este antes de resetear el
# estado: si ya hay un turno mas nuevo, no lo pisa.
_turno_gen = {"n": 0}


def _set_estado(nuevo):
    with _estado_lock:
        _estado_turno["valor"] = nuevo


def _set_estado_si(actual, nuevo):
    """Cambia el estado a `nuevo` solo si ahora mismo es `actual` (compare-and-set
    atomico)."""
    with _estado_lock:
        if _estado_turno["valor"] == actual:
            _estado_turno["valor"] = nuevo


def _estado_actual():
    with _estado_lock:
        return _estado_turno["valor"]


def _es_turno_actual(gen):
    with _estado_lock:
        return gen == _turno_gen["n"]


def _icono_circulo(color):
    imagen = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).ellipse((8, 8, 56, 56), fill=color)
    return imagen


ICONO_INACTIVO = _icono_circulo((128, 128, 128, 255))  # gris: esperando que apretes la tecla
ICONO_ESCUCHANDO = _icono_circulo((30, 144, 255, 255))  # azul: grabando
ICONO_PROCESANDO = _icono_circulo((255, 140, 0, 255))  # naranja: transcribiendo

# nombre de estado (el que entiende la ventana de chat) -> icono de bandeja
_ICONOS_ESTADO = {
    "inactivo": ICONO_INACTIVO,
    "escuchando": ICONO_ESCUCHANDO,
    "procesando": ICONO_PROCESANDO,
}


class _LocutorStreaming:
    """Dice cada oración apenas el modelo la genera, en un hilo aparte, así la
    voz y la generación del LLM se solapan (activado con llm.voz_streaming en
    config.json). Si apretás F9 mientras habla (barge-in), deja de decir lo
    que quede en cola."""

    _FIN = object()

    def __init__(self):
        reiniciar_barge_in()  # arrancamos un turno nuevo; hablar() no la va a limpiar
        self._cola = queue.Queue()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def _bucle(self):
        while True:
            item = self._cola.get()
            if item is self._FIN:
                return
            if not barge_in_pedido():
                hablar(item, reiniciar_cancelacion=False)

    def decir(self, oracion):
        """Callback para motor_llm.ejecutar_turno(al_oracion=...). Devuelve
        False si hubo barge-in, para que el motor corte la generación."""
        self._cola.put(oracion)
        return not barge_in_pedido()

    def terminar(self):
        """Espera a que se termine de decir lo que quede en cola."""
        self._cola.put(self._FIN)
        self._hilo.join()


def _procesar(audio, modelo, mostrar_en_chat=None):
    texto = transcribir(audio, modelo)
    if not texto:
        LOG.info("(audio sin texto reconocible)")
        return

    LOG.info('Comando: "%s"', texto)
    if mostrar_en_chat:
        mostrar_en_chat("usuario", texto)  # el turno de voz también se ve en el chat

    locutor = _LocutorStreaming() if config.obtener("llm.voz_streaming", False) else None
    if locutor:
        _set_estado(ESTADO_HABLANDO)  # ya se empieza a hablar mientras el LLM genera

    t0 = time.time()
    try:
        respuesta, llamadas = motor_llm.ejecutar_turno(
            texto, skills.activas(), al_oracion=locutor.decir if locutor else None
        )
    except Exception:  # una skill puede fallar (app no encontrada, etc.)
        LOG.exception("Error ejecutando el turno")
        if locutor:
            locutor.terminar()
        _set_estado(ESTADO_HABLANDO)
        if mostrar_en_chat:
            mostrar_en_chat("asistente", "Hubo un error, no pude hacerlo.")
        hablar("Hubo un error, no pude hacerlo.")
        return
    duracion = time.time() - t0

    for llamada in llamadas:
        LOG.info("  Tool: %s(%s) -> %s", llamada["tool"], llamada["args"], llamada["resultado"])
    LOG.info('Respuesta (%.2fs): "%s"', duracion, respuesta)

    if respuesta and mostrar_en_chat:
        mostrar_en_chat("asistente", respuesta)

    if locutor:
        locutor.terminar()  # el texto ya se fue diciendo por oraciones
    elif respuesta:
        _set_estado(ESTADO_HABLANDO)
        hablar(respuesta)


def _franja_horaria():
    hora = time.localtime().tm_hour
    if hora < 12:
        return "Buenos días"
    if hora < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _saludar():
    """Saludo hablado al arrancar (estilo Jarvis). Franja horaria + una
    frase al azar de config.json (saludo.frases). Corre en un hilo aparte
    apenas aparece el icono, EN PARALELO a la carga de los modelos -- si
    no, el saludo salia recien despues de cargar Gemma (10-30s en GPU).
    Por eso no usa el LLM aca."""
    if not config.obtener("saludo.al_iniciar", True):
        return
    _set_estado(ESTADO_HABLANDO)
    try:
        frases = config.obtener("saludo.frases") or ["A sus órdenes."]
        hablar(f"{_franja_horaria()} señor. {random.choice(frases)}")
    except Exception:
        LOG.exception("Fallo el saludo")
    finally:
        _set_estado_si(ESTADO_HABLANDO, ESTADO_IDLE)


def _arrancar(cambiar_estado, mostrar_en_chat, api):
    """Carga los modelos y engancha el push-to-talk. Lo corre webview.start()
    en un hilo aparte, así la carga de Gemma (10-30s en GPU) no traba el loop
    de la ventana. `cambiar_estado` actualiza icono de bandeja + cartel de la
    ventana; se llama desde el hilo del hook de teclado (evaluate_js de
    pywebview es thread-safe, no hace falta marshalling). `api` se usa para
    engancharle al botón de micrófono del chat el mismo flujo que F9."""

    # La voz (Piper) se carga en paralelo con Whisper/Gemma.
    threading.Thread(target=precargar_tts, daemon=True).start()

    LOG.info("Cargando modelo STT...")
    modelo = cargar_modelo_stt()
    LOG.info("Cargando modelo LLM (Gemma 4 E2B, GPU)...")
    motor_llm.cargar()
    LOG.info('Listo. Mantene "%s" apretada para hablar (revisa el icono en la bandeja).', TECLA_PTT.upper())

    global _STREAM_AUDIO
    estado = EstadoGrabacion()
    _STREAM_AUDIO = iniciar_stream(estado)

    tecla_abajo = {"activa": False}

    def al_presionar(_evento):
        if tecla_abajo["activa"]:
            return  # repeticion del SO mientras se mantiene apretada
        est = _estado_actual()
        if est == ESTADO_PROCESANDO:
            return  # transcribiendo/pensando: no se interrumpe
        if est == ESTADO_HABLANDO:
            detener_reproduccion()  # barge-in: cortar la respuesta en seco
        tecla_abajo["activa"] = True
        estado.iniciar()
        cambiar_estado("escuchando")
        LOG.info("Escuchando...")

    def procesar_en_hilo_aparte(audio, gen):
        try:
            _procesar(audio, modelo, mostrar_en_chat)
        finally:
            # Solo resetear si sigue siendo el turno mas nuevo y no arranco
            # otra grabacion (barge-in mientras esta funcion seguia corriendo).
            if _es_turno_actual(gen) and not estado.grabando:
                _set_estado(ESTADO_IDLE)
                cambiar_estado("inactivo")

    def al_soltar(_evento):
        if not tecla_abajo["activa"]:
            return
        tecla_abajo["activa"] = False
        audio = estado.detener()
        with _estado_lock:
            _turno_gen["n"] += 1
            gen = _turno_gen["n"]
            _estado_turno["valor"] = ESTADO_PROCESANDO
        cambiar_estado("procesando")
        # Despachar a un hilo aparte para no bloquear el hilo del hook de
        # teclado (Windows puede desenganchar un hook que tarda demasiado en
        # responder).
        threading.Thread(target=procesar_en_hilo_aparte, args=(audio, gen), daemon=True).start()

    keyboard.on_press_key(TECLA_PTT, al_presionar)
    keyboard.on_release_key(TECLA_PTT, al_soltar)

    # El botón de micrófono del chat dispara exactamente lo mismo que F9
    # (apretar = empezar a grabar, soltar = cortar y procesar).
    api._mic_iniciar = lambda: al_presionar(None)
    api._mic_finalizar = lambda: al_soltar(None)


def main():
    LOG.info('=== Agente base + chat -- mantene "%s" para hablar, o escribi en la ventana ===', TECLA_PTT.upper())

    api, ventana = chat_web.crear()  # ventana WebView, todavía sin arrancar el loop

    def cambiar_estado(nombre):
        try:
            icono.icon = _ICONOS_ESTADO.get(nombre, ICONO_INACTIVO)
        except Exception:
            pass
        api.set_estado_ptt(nombre)

    def mostrar_en_chat(quien, texto):
        api.agregar(quien, texto)

    # Saludar y abrir la rutina de inicio YA, mientras cargan los modelos
    # (edge-tts, abrir apps y Spotify no dependen de Whisper/Gemma) -- asi
    # abrir Viernes no se siente lento.
    threading.Thread(target=_saludar, daemon=True).start()
    # Indexar las apps instaladas ya, en paralelo con la carga de modelos, para
    # que el primer "abri X" de la sesion no espere el escaneo del Menu Inicio.
    threading.Thread(target=skills.apps.precargar, daemon=True).start()
    if config.obtener("rutina.al_iniciar", True):
        threading.Thread(target=skills.rutina.ejecutar_al_inicio, daemon=True).start()

    def _mostrar(_icon, _item):
        ventana.show()

    def _salir(_icon, _item):
        ventana.destroy()

    icono = pystray.Icon(
        "agente",
        icon=ICONO_INACTIVO,
        title="Viernes",
        menu=pystray.Menu(
            pystray.MenuItem("Mostrar chat", _mostrar, default=True),
            pystray.MenuItem("Salir", _salir),
        ),
    )
    icono.run_detached()

    try:
        # webview.start() bloquea el hilo principal y corre _arrancar en un
        # hilo aparte una vez que la ventana está lista. Vuelve cuando se
        # cierra la ventana (X o "Salir" de la bandeja).
        webview.start(lambda: _arrancar(cambiar_estado, mostrar_en_chat, api), debug=False)
    finally:
        keyboard.unhook_all()
        if _STREAM_AUDIO is not None:
            try:
                _STREAM_AUDIO.stop()
                _STREAM_AUDIO.close()
            except Exception:
                pass
        icono.stop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    main()
