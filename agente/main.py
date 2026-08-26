"""Agente base -- Fase 2 del plan:
F9 graba, Whisper transcribe, el motor LLM (Gemma 4 E2B via litert-lm-api
en GPU -- ver core/motor_llm.py) decide si responde directo o llama una
skill, y la respuesta se dice en voz alta. Reemplaza el "eco" de la Fase 1
una vez confirmado que el pipeline de audio andaba bien.

Proyecto separado de Viernes por diseno: no importa nada de
fase1-4/main.py/fase6-10 de la carpeta padre, para poder moverse a su
propia carpeta/repo con git mv sin arrastrar el mouse gestual."""

import sys
import threading
import time

import keyboard
import pystray
from PIL import Image, ImageDraw

import skills
from core import logs, motor_llm
from core.voz import EstadoGrabacion, cargar_modelo_stt, detener_reproduccion, hablar, iniciar_stream, transcribir

TECLA_PTT = "f9"

LOG = logs.configurar()

# Referencia global al stream de audio: sin esto, el objeto sd.InputStream
# que devuelve iniciar_stream() no tiene ningun referente en Python y puede
# destruirse mientras sigue activo -- eso corrompe el heap y crashea el
# proceso con STATUS_BREAKPOINT (visto durante el desarrollo de este
# archivo). fase10_asistente_ptt.py no tiene este problema porque crea el
# stream directo en setup() y lo mantiene como variable local ahi mismo.
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


def _icono_circulo(color):
    imagen = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).ellipse((8, 8, 56, 56), fill=color)
    return imagen


ICONO_INACTIVO = _icono_circulo((128, 128, 128, 255))  # gris: esperando que apretes la tecla
ICONO_ESCUCHANDO = _icono_circulo((30, 144, 255, 255))  # azul: grabando
ICONO_PROCESANDO = _icono_circulo((255, 140, 0, 255))  # naranja: transcribiendo


def _procesar(audio, modelo):
    texto = transcribir(audio, modelo)
    if not texto:
        LOG.info("(audio sin texto reconocible)")
        return

    LOG.info('Comando: "%s"', texto)
    t0 = time.time()
    try:
        respuesta, llamadas = motor_llm.ejecutar_turno(texto, skills.TOOLS)
    except Exception:  # una skill puede fallar (app no encontrada, etc.)
        LOG.exception("Error ejecutando el turno")
        _estado_turno["valor"] = ESTADO_HABLANDO
        hablar("Hubo un error, no pude hacerlo.")
        return
    duracion = time.time() - t0

    for llamada in llamadas:
        LOG.info("  Tool: %s(%s) -> %s", llamada["tool"], llamada["args"], llamada["resultado"])
    LOG.info('Respuesta (%.2fs): "%s"', duracion, respuesta)

    if respuesta:
        _estado_turno["valor"] = ESTADO_HABLANDO
        hablar(respuesta)


def setup(icon):
    icon.visible = True
    LOG.info('=== Agente base -- mantene "%s" para hablar ===', TECLA_PTT.upper())
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
        if _estado_turno["valor"] == ESTADO_PROCESANDO:
            return  # transcribiendo/pensando: no se interrumpe
        if _estado_turno["valor"] == ESTADO_HABLANDO:
            detener_reproduccion()  # barge-in: cortar la respuesta en seco
        tecla_abajo["activa"] = True
        estado.iniciar()
        icon.icon = ICONO_ESCUCHANDO
        LOG.info("Escuchando...")

    def procesar_en_hilo_aparte(audio):
        try:
            _procesar(audio, modelo)
        finally:
            # Si ya arranco una grabacion nueva (barge-in mientras esta
            # funcion segui corriendo), no pisar ese estado/icono.
            if not estado.grabando:
                _estado_turno["valor"] = ESTADO_IDLE
                icon.icon = ICONO_INACTIVO

    def al_soltar(_evento):
        if not tecla_abajo["activa"]:
            return
        tecla_abajo["activa"] = False
        audio = estado.detener()
        _estado_turno["valor"] = ESTADO_PROCESANDO
        icon.icon = ICONO_PROCESANDO
        # Igual que en fase10: despachar a un hilo aparte para no bloquear
        # el hilo del hook de teclado (Windows puede desenganchar un hook
        # que tarda demasiado en responder).
        threading.Thread(target=procesar_en_hilo_aparte, args=(audio,), daemon=True).start()

    keyboard.on_press_key(TECLA_PTT, al_presionar)
    keyboard.on_release_key(TECLA_PTT, al_soltar)


def _salir(icon, _item):
    keyboard.unhook_all()
    icon.stop()


def main():
    icon = pystray.Icon(
        "agente",
        icon=ICONO_INACTIVO,
        title="Agente (Fase 2)",
        menu=pystray.Menu(pystray.MenuItem("Salir", _salir)),
    )
    icon.run(setup=setup)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    main()
