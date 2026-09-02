"""Motor LLM del agente: carga Gemma 4 E2B una sola vez via litert-lm-api,
en GPU -- el motor ganador de la comparativa inicial (10/10 de precision en
las 10 frases de prueba, 1-2s por comando en GPU vs 5-9s en CPU).

ejecutar_turno() manda un comando de voz con las skills registradas y
devuelve el texto a decir en voz alta. Si el modelo ejecuta una tool y no
agrega comentario propio (pasa seguido con acciones simples como abrir una
app), cae al texto que devolvio la tool misma en vez de quedarse mudo."""

import logging
import re
import threading

from litert_lm import Backend, Engine, interfaces

import config
from core import memoria

_LOG = logging.getLogger("agente.motor_llm")

# Ruta al .litertlm y backend salen de config.json (ver config.example.json).
MODEL_PATH = config.obtener("llm.model_path")
_BACKEND = config.obtener("llm.backend", "gpu")


def _backend():
    return Backend.CPU() if str(_BACKEND).lower() == "cpu" else Backend.GPU()

SYSTEM_MESSAGE = (
    "Sos Viernes, un asistente de voz en espanol que corre en la PC del "
    "usuario. Respondes siempre en espanol, de forma breve porque tu "
    "respuesta se lee en voz alta. Si el pedido corresponde a una accion "
    "que tenes disponible como herramienta, llamala. Si es una pregunta de "
    "conocimiento general que ya sabes responder, respondela directo sin "
    "usar ninguna herramienta. Si no sabes algo actualizado (clima, "
    "noticias, informacion reciente) o el usuario pide buscar algo, usa "
    "buscar_en_internet en vez de inventar una respuesta. Solo usa "
    "ChatGPT si el usuario lo nombra explicitamente. Tenes memoria de esta "
    "conversacion: usa el contexto previo para resolver referencias como "
    "'si', 'esa', 'la siguiente', 'lo que dijiste'."
)

# El resumidor es el mismo Gemma 4 E2B (modelo chico, resume con imprecisiones):
# por eso se le pide poco y concreto, y motor_llm siempre deja los ultimos
# turnos verbatim para que un resumen flojo no rompa las referencias cercanas.
RESUMIDOR_SYSTEM = (
    "Sos un compresor de conversaciones. Te paso un resumen previo (puede "
    "estar vacio) y los turnos nuevos de una charla entre un usuario y su "
    "asistente. Devolve UN solo parrafo en espanol, en tercera persona, que "
    "actualice el resumen conservando datos concretos: nombres, numeros, "
    "canciones, apps, decisiones tomadas y lo que quedo pendiente. No "
    "inventes nada. No saludes ni expliques, solo el parrafo."
)

_engine = None

# Serializa los turnos: el motor se usa desde dos lados (push-to-talk por voz
# en main.py y la ventana de chat), y tocar el mismo Engine desde dos hilos a
# la vez es justo lo que el resto del codigo evita. Cubre tanto la creacion
# perezosa del Engine como cada turno. No es reentrante: cargar() y
# ejecutar_turno() nunca se llaman uno dentro del otro bajo el candado.
_lock = threading.Lock()


class _RegistradorTools(interfaces.ToolEventHandler):
    """Guarda cada tool llamada (nombre, argumentos, resultado) -- tanto
    para el fallback de respuesta hablada (ver ejecutar_turno) como para
    que quien llame pueda loguear el detalle de la ejecucion."""

    def __init__(self):
        self.llamadas = []

    def approve_tool_call(self, tool_call):
        funcion = tool_call.get("function", {})
        self.llamadas.append(
            {"tool": funcion.get("name"), "args": funcion.get("arguments"), "resultado": None}
        )
        return True

    def process_tool_response(self, tool_response):
        if self.llamadas:
            self.llamadas[-1]["resultado"] = tool_response
        return tool_response

    @property
    def ultimo_resultado(self):
        return self.llamadas[-1]["resultado"] if self.llamadas else None


def cargar():
    """Carga el modelo una sola vez. Llamar al arrancar el programa, antes
    de ejecutar_turno() (si no se llamo antes, ejecutar_turno la crea sola,
    pero entonces el primer comando paga el costo de carga)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = Engine(MODEL_PATH, backend=_backend())
    return _engine


# Corta el texto que va llegando del modelo en oraciones completas (termina
# en . ! ? … o salto de linea). El modo streaming va diciendo cada una apenas
# esta lista, en vez de esperar la respuesta entera.
_FIN_ORACION = re.compile(r".+?(?:[.!?…]+(?=\s|$)|\n)", re.S)


def _partir_oraciones(buffer):
    """(oraciones_completas, resto_sin_terminar)."""
    oraciones, pos = [], 0
    for m in _FIN_ORACION.finditer(buffer):
        fragmento = m.group(0).strip()
        if fragmento:
            oraciones.append(fragmento)
        pos = m.end()
    return oraciones, buffer[pos:]


def _consumir_stream(conversacion, texto, al_oracion):
    """Consume la respuesta del modelo a medida que se genera y llama a
    `al_oracion(oracion)` por cada oracion completa. Si `al_oracion` devuelve
    False, cancela la generacion. Devuelve el texto completo."""
    completo, buffer = "", ""
    for msg in conversacion.send_message_async(texto):
        nuevo = ""
        for bloque in msg.get("content", []) or []:
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                nuevo += bloque.get("text", "")
        if not nuevo:
            continue
        # Algunos backends mandan el texto acumulado en vez del delta.
        if completo and nuevo.startswith(completo):
            nuevo = nuevo[len(completo):]
        completo += nuevo
        buffer += nuevo
        oraciones, buffer = _partir_oraciones(buffer)
        for oracion in oraciones:
            if al_oracion(oracion) is False:
                try:
                    conversacion.cancel_process()
                except Exception:
                    pass
                return completo.strip()
    resto = buffer.strip()
    if resto:
        al_oracion(resto)
    return completo.strip()


def _resumir(resumen_previo, turnos):
    """Regenera el resumen de la conversacion con el mismo modelo. Se llama
    con `_lock` ya tomado (desde ejecutar_turno). Devuelve el parrafo nuevo o
    el resumen previo si algo falla (nunca revienta el turno)."""
    if not turnos:
        return resumen_previo
    lineas = [f"Resumen previo: {resumen_previo or '(vacio)'}", "", "Turnos nuevos:"]
    for t in turnos:
        quien = "Usuario" if t["rol"] == "usuario" else "Asistente"
        lineas.append(f"{quien}: {t['texto']}")
    try:
        conv = _engine.create_conversation(
            system_message=RESUMIDOR_SYSTEM, automatic_tool_calling=False
        )
        respuesta = conv.send_message("\n".join(lineas))
        nuevo = ""
        for bloque in respuesta.get("content", []):
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                nuevo += bloque.get("text", "")
        return nuevo.strip() or resumen_previo
    except Exception:
        return resumen_previo


def ejecutar_turno(texto, tools, al_oracion=None):
    """Manda `texto` al modelo con las `tools` (funciones Python, con type
    hints y docstring Args: para que litert-lm-api arme bien el schema).
    Devuelve (texto_respuesta, llamadas) -- llamadas es una lista de
    {"tool", "args", "resultado"} por cada tool que se ejecuto (vacia si
    el modelo respondio directo), para poder loguear el detalle.

    Si se pasa `al_oracion`, la respuesta se genera en streaming y se llama
    a `al_oracion(oracion)` por cada oracion completa (para ir diciendola en
    voz alta sin esperar el final). Devolver False desde ahi corta la
    generacion."""
    global _engine

    registrador = _RegistradorTools()
    memoria.rotar_si_corresponde()
    previos = memoria.mensajes_previos()
    if previos:
        _LOG.info(
            "Contexto: %d mensajes previos%s",
            len(previos), " (incluye resumen)" if memoria.resumen_actual() else "",
        )
    with _lock:
        if _engine is None:  # primer turno sin cargar() previo: se crea aca
            _engine = Engine(MODEL_PATH, backend=_backend())
        conversacion = _engine.create_conversation(
            messages=previos or None,
            tools=tools,
            system_message=SYSTEM_MESSAGE,
            automatic_tool_calling=True,
            tool_event_handler=registrador,
        )
        if al_oracion is None:
            respuesta = conversacion.send_message(texto)
        else:
            respuesta = {
                "content": [{"type": "text", "text": _consumir_stream(conversacion, texto, al_oracion)}]
            }

    texto_respuesta = ""
    for bloque in respuesta.get("content", []):
        if isinstance(bloque, dict) and bloque.get("type") == "text":
            texto_respuesta += bloque.get("text", "")
    texto_respuesta = texto_respuesta.strip()

    if not texto_respuesta and registrador.ultimo_resultado:
        texto_respuesta = str(registrador.ultimo_resultado)
        if al_oracion is not None:  # una tool sin comentario del modelo: decir su resultado
            al_oracion(texto_respuesta)

    memoria.registrar(texto, texto_respuesta)
    if memoria.necesita_resumen():
        # Compresion progresiva: pasa cada ~ventana_turnos turnos y suma
        # ~1-2s a ese turno puntual. Toma _lock de nuevo (se usa _engine).
        _LOG.info("La conversacion supero la ventana: comprimiendo lo viejo en un resumen...")
        with _lock:
            memoria.aplicar_resumen(
                _resumir(memoria.resumen_actual(), memoria.turnos_a_comprimir())
            )

    return texto_respuesta, registrador.llamadas
