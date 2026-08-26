"""Motor LLM del agente: carga Gemma 4 E2B una sola vez via litert-lm-api,
en GPU -- el motor ganador de la Fase 0 del plan (ver
privado/spikes/spike_litertlm.py: 10/10 de precision en las 10 frases de
prueba, 1-2s por comando en GPU vs 5-9s en CPU).

ejecutar_turno() manda un comando de voz con las skills registradas y
devuelve el texto a decir en voz alta. Si el modelo ejecuta una tool y no
agrega comentario propio (pasa seguido con acciones simples como abrir una
app), cae al texto que devolvio la tool misma en vez de quedarse mudo."""

from litert_lm import Backend, Engine, interfaces

MODEL_PATH = (
    r"C:\Users\usuario\Documents\Proyectos\viernes\privado\modelos_hf"
    r"\models--litert-community--gemma-4-E2B-it-litert-lm\snapshots"
    r"\6b78abd019e61a1ca4cbe3b212d2c9ce8ff38a94\gemma-4-E2B-it.litertlm"
)

SYSTEM_MESSAGE = (
    "Sos Viernes, un asistente de voz en espanol que corre en la PC del "
    "usuario. Respondes siempre en espanol, de forma breve porque tu "
    "respuesta se lee en voz alta. Si el pedido corresponde a una accion "
    "que tenes disponible como herramienta, llamala. Si es una pregunta de "
    "conocimiento general que ya sabes responder, respondela directo sin "
    "usar ninguna herramienta. Si no sabes algo actualizado (clima, "
    "noticias, informacion reciente) o el usuario pide explicitamente "
    "buscar en internet, usa la herramienta correspondiente en vez de "
    "inventar una respuesta."
)

_engine = None


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
    de ejecutar_turno() (si no se llamo antes, ejecutar_turno la llama
    sola, pero entonces el primer comando paga el costo de carga)."""
    global _engine
    if _engine is None:
        _engine = Engine(MODEL_PATH, backend=Backend.GPU())
    return _engine


def ejecutar_turno(texto, tools):
    """Manda `texto` al modelo con las `tools` (funciones Python, con type
    hints y docstring Args: para que litert-lm-api arme bien el schema).
    Devuelve (texto_respuesta, llamadas) -- llamadas es una lista de
    {"tool", "args", "resultado"} por cada tool que se ejecuto (vacia si
    el modelo respondio directo), para poder loguear el detalle."""
    if _engine is None:
        cargar()

    registrador = _RegistradorTools()
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
