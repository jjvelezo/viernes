"""Primitivas de audio del agente: grabacion push-to-talk, STT y TTS.

Patron ya validado en un prototipo previo (sd.InputStream + callback,
WhisperModel "small" en CPU, edge-tts + MCI para la respuesta hablada) y
reescrito aca. Historial de voces de TTS probadas y por que el mp3 se
reproduce con MCI y no con "play wait": CLAUDE.md."""

import asyncio
import ctypes
import tempfile
import threading
import time
from pathlib import Path

import edge_tts
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

import config

MUESTREO_HZ = 16000
MODELO_STT = config.obtener("stt.model", "small")

# Whisper mezcla espanol con nombres de marcas/apps en ingles bastante mal
# ("chrome" -> "chorme", etc). initial_prompt le da a Whisper una lista de
# palabras esperadas para sesgar el reconocimiento hacia ellas -- no
# garantiza que las reconozca bien, pero ayuda. Agregar acá los nombres
# nuevos que se sumen a APPS_CONOCIDAS en skills/apps.py.
PROMPT_INICIAL = (
    "Comandos de voz en español con nombres de aplicaciones: Chrome, Notion, "
    "Brave, Edge, Firefox, Discord, WhatsApp, Telegram, Spotify, Word, Excel, "
    "Outlook, Teams, Visual Studio Code, ChatGPT."
)

VOZ_TTS = config.obtener("tts.voice", "es-MX-DaliaNeural")  # historial de voces probadas: CLAUDE.md
RATE_TTS = config.obtener("tts.rate", "+15%")


class EstadoGrabacion:
    """Estado compartido entre el callback de audio (hilo de sounddevice) y
    quien dispare grabar/dejar de grabar (ej. un hotkey)."""

    def __init__(self):
        self.grabando = False
        self.fragmentos = []

    def callback_audio(self, indata, frames, time_info, status):
        if self.grabando:
            self.fragmentos.append(indata.copy())

    def iniciar(self):
        self.fragmentos = []
        self.grabando = True

    def detener(self):
        """Corta la grabacion y devuelve el audio acumulado como un array
        float32 mono."""
        self.grabando = False
        if not self.fragmentos:
            return np.zeros(0, dtype="float32")
        return np.concatenate(self.fragmentos, axis=0).flatten()


def iniciar_stream(estado):
    """Arranca el InputStream de sounddevice, corriendo todo el tiempo
    desde el arranque -- el callback solo junta audio mientras
    estado.grabando este activo."""
    stream = sd.InputStream(
        samplerate=MUESTREO_HZ, channels=1, dtype="float32", callback=estado.callback_audio
    )
    stream.start()
    return stream


def cargar_modelo_stt():
    return WhisperModel(MODELO_STT, device="cpu", compute_type="int8")


UMBRAL_SILENCIO = 0.01  # pico de amplitud por debajo del cual el audio es silencio


def _es_eco_del_prompt(texto):
    """Whisper, sobre silencio/ruido, a veces devuelve como transcripción el
    propio `initial_prompt` (o un pedazo). Eso NO es un comando."""
    t = texto.lower().strip().rstrip(".")
    return len(t) > 8 and t in PROMPT_INICIAL.lower()


def transcribir(audio, modelo):
    """Devuelve el texto transcripto, o "" si el audio es muy corto (toque
    sin querer), silencio, o no se entendio nada."""
    if audio.size < MUESTREO_HZ * 0.3:
        return ""
    if float(np.max(np.abs(audio))) < UMBRAL_SILENCIO:
        return ""  # no llegó a hablar (apretó y soltó F9 sin decir nada)
    segmentos, _info = modelo.transcribe(
        audio,
        language="es",
        initial_prompt=PROMPT_INICIAL,
        vad_filter=True,  # descarta tramos sin voz -> menos alucinaciones
        condition_on_previous_text=False,
    )
    texto = " ".join(segmento.text.strip() for segmento in segmentos).strip()
    if _es_eco_del_prompt(texto):
        return ""
    return texto


ALIAS_MCI = "voz_agente"

# Se activa cuando alguien llama detener_reproduccion() (barge-in: la
# persona empieza a hablar mientras el agente todavia contesta). hablar()
# lo limpia al arrancar y lo chequea DESPUES de generar el mp3 -- si no,
# un barge-in que cae mientras edge-tts todavia esta generando el audio
# (1-3s) no cortaba nada y la voz igual arrancaba pisando al usuario.
_cancelado = threading.Event()


def _reproducir_mp3(ruta):
    """Reproduce un mp3 con el driver MCI de Windows -- sin dependencias
    nuevas ademas de edge-tts, que no sabe decodificar/reproducir audio.

    Ojo: NO se usa "play ... wait". Ese bloqueo no se corta de forma
    confiable cuando otro hilo manda "stop" sobre el mismo alias (MCI se
    queda pegado). En vez de eso se lanza el play sin esperar y se sondea
    el estado cada 50ms, saliendo apenas termina o apenas se pide un
    barge-in (_cancelado) -- asi la voz se calla en ~50ms, no al final."""
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f'open "{ruta}" type mpegvideo alias {ALIAS_MCI}', None, 0, None)
    mci(f"play {ALIAS_MCI}", None, 0, None)
    buffer = ctypes.create_unicode_buffer(32)
    while not _cancelado.is_set():
        mci(f"status {ALIAS_MCI} mode", buffer, 32, None)
        if buffer.value != "playing":
            break
        time.sleep(0.05)
    mci(f"stop {ALIAS_MCI}", None, 0, None)
    mci(f"close {ALIAS_MCI}", None, 0, None)


def detener_reproduccion():
    """Corta en seco la reproduccion en curso de hablar(), si hay una --
    para barge-in (la persona aprieta F9 mientras el agente todavia esta
    contestando). Segura de llamar aunque no haya nada reproduciendose
    (MCI devuelve un error que se ignora)."""
    _cancelado.set()  # corta tambien si todavia estamos generando el mp3
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f"stop {ALIAS_MCI}", None, 0, None)
    mci(f"close {ALIAS_MCI}", None, 0, None)


def _generar_mp3(texto, ruta_destino):
    """Corre la generacion de audio (asyncio) en un hilo nuevo y aislado.
    Necesario porque si el turno uso preguntar_internet (Playwright) antes
    de llegar aca, el hilo que procesa el turno puede quedar con el manejo
    interno de asyncio "contaminado" por Playwright, y un asyncio.run()
    directo en ese mismo hilo revienta con RuntimeError ("cannot be called
    from a running event loop") -- visto en la practica. Un hilo nuevo
    arranca siempre con un estado de asyncio limpio, sin depender de
    entender el detalle exacto de que deja mal configurado Playwright."""

    def _tarea():
        comunicador = edge_tts.Communicate(texto, VOZ_TTS, rate=RATE_TTS)
        asyncio.run(comunicador.save(str(ruta_destino)))

    hilo = threading.Thread(target=_tarea)
    hilo.start()
    hilo.join()


def hablar(texto):
    """Convierte `texto` a voz (edge-tts) y lo reproduce. Genera un mp3
    temporal y lo borra al terminar. Si llega un barge-in
    (detener_reproduccion) mientras se genera el audio, no llega a sonar."""
    _cancelado.clear()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
        ruta_temp = Path(temporal.name)
    try:
        _generar_mp3(texto, ruta_temp)
        if _cancelado.is_set():
            return  # el usuario ya empezo a hablar: no pisar con la voz
        _reproducir_mp3(ruta_temp)
    finally:
        ruta_temp.unlink(missing_ok=True)
