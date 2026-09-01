"""Motor LLM del agente: carga Gemma 4 E2B una sola vez via litert-lm-api,
en GPU -- el motor ganador de la comparativa inicial (10/10 de precision en
las 10 frases de prueba, 1-2s por comando en GPU vs 5-9s en CPU).

ejecutar_turno() manda un comando de voz con las skills registradas y
devuelve el texto a decir en voz alta. Si el modelo ejecuta una tool y no
agrega comentario propio (pasa seguido con acciones simples como abrir una
app), cae al texto que devolvio la tool misma en vez de quedarse mudo."""

import threading

from litert_lm import Backend, Engine, interfaces

import config

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
    "ChatGPT si el usuario lo nombra explicitamente."
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


def ejecutar_turno(texto, tools):
    """Manda `texto` al modelo con las `tools` (funciones Python, con type
    hints y docstring Args: para que litert-lm-api arme bien el schema).
    Devuelve (texto_respuesta, llamadas) -- llamadas es una lista de
    {"tool", "args", "resultado"} por cada tool que se ejecuto (vacia si
    el modelo respondio directo), para poder loguear el detalle."""
    global _engine

    registrador = _RegistradorTools()
    with _lock:
        if _engine is None:  # primer turno sin cargar() previo: se crea aca
            _engine = Engine(MODEL_PATH, backend=_backend())
        conversacion = _engine.create_conversation(
            tools=tools,
            system_message=SYSTEM_MESSAGE,
            automatic_tool_calling=True,
            tool_event_handler=registrador,
        )
        respuesta = conversacion.send_message(texto)

    texto_respuesta = ""
    for bloque in respuesta.get("content", []):
        if isinstance(bloque, dict) and bloque.get("type") == "text":
            texto_respuesta += bloque.get("text", "")
    texto_respuesta = texto_respuesta.strip()

    if not texto_respuesta and registrador.ultimo_resultado:
        texto_respuesta = str(registrador.ultimo_resultado)

    return texto_respuesta, registrador.llamadas
