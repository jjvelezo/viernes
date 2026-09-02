"""Primitivas de audio del agente: grabacion push-to-talk, STT y TTS.

Patron ya validado en un prototipo previo (sd.InputStream + callback,
WhisperModel "small" en CPU, edge-tts + MCI para la respuesta hablada) y
reescrito aca. Historial de voces de TTS probadas y por que el mp3 se
reproduce con MCI y no con "play wait": docs/decisiones.md."""

import asyncio
import ctypes
import hashlib
import logging
import tempfile
import threading
import time
from pathlib import Path

import edge_tts
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

import config

_LOG = logging.getLogger("agente.voz")

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

# Ajustes de voz: se leen de config.json en cada sintesis (no una sola vez al
# importar) para que el panel de personalizacion del chat tenga efecto en la
# proxima respuesta sin reiniciar Viernes.
#
# Motor de TTS (`tts.motor`):
#   "auto"  -> Piper (local, offline) si hay modelo; si no, edge-tts.
#   "piper" -> siempre Piper (si falla la sintesis, cae a edge igual).
#   "edge"  -> siempre edge-tts (necesita internet, suena un poco mejor).
#
# Velocidad:
#   Piper -> `tts.piper_length_scale` (length_scale del modelo, MENOR = MAS
#            RAPIDO; 0.8 ~= 15% mas rapido que el default 1.0; bajar de ~0.7
#            suena atropellado).
#   edge  -> `tts.rate` ("+15%", "-10%"...). El panel del chat mueve las dos
#            con un solo control.
def _voz_tts():
    return config.obtener("tts.voice", "es-MX-DaliaNeural")  # voces probadas: docs/decisiones.md


def _rate_tts():
    return config.obtener("tts.rate", "+15%")


def _motor_tts():
    return str(config.obtener("tts.motor", "auto")).lower()


def _piper_length_scale():
    return float(config.obtener("tts.piper_length_scale", 0.8))

_CARPETA_VOZ_PIPER = Path(__file__).resolve().parent.parent.parent / "privado" / "voz_piper"


def _ruta_modelo_piper():
    """Ruta al .onnx de Piper: la de config si esta, si no el primer .onnx
    dentro de privado/voz_piper/. None si no hay ninguno."""
    configurada = config.obtener("tts.piper_model")
    if configurada:
        p = Path(configurada)
        return p if p.exists() else None
    if _CARPETA_VOZ_PIPER.is_dir():
        modelos = sorted(_CARPETA_VOZ_PIPER.glob("*.onnx"))
        if modelos:
            return modelos[0]
    return None


# Cache de frases ya sintetizadas (saludos, "listo", confirmaciones de
# volumen...). Se guarda el audio la primera vez y se reproduce de disco las
# siguientes -> instantaneo y sin depender de internet. Solo frases cortas:
# las respuestas largas rara vez se repiten palabra por palabra.
_CACHE_TTS = Path(__file__).resolve().parent.parent.parent / "privado" / "tts_cache"
_LARGO_MAX_CACHE = 140


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


def _reproducir_audio(ruta):
    """Reproduce un mp3 o wav con el driver MCI de Windows.

    Ojo: NO se usa "play ... wait". Ese bloqueo no se corta de forma
    confiable cuando otro hilo manda "stop" sobre el mismo alias (MCI se
    queda pegado). En vez de eso se lanza el play sin esperar y se sondea
    el estado cada 50ms, saliendo apenas termina o apenas se pide un
    barge-in (_cancelado) -- asi la voz se calla en ~50ms, no al final."""
    ruta = Path(ruta)
    tipo = "waveaudio" if ruta.suffix.lower() == ".wav" else "mpegvideo"
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f'open "{ruta}" type {tipo} alias {ALIAS_MCI}', None, 0, None)
    mci(f"play {ALIAS_MCI}", None, 0, None)
    buffer = ctypes.create_unicode_buffer(32)
    while not _cancelado.is_set():
        mci(f"status {ALIAS_MCI} mode", buffer, 32, None)
        if buffer.value != "playing":
            break
        time.sleep(0.05)
    mci(f"stop {ALIAS_MCI}", None, 0, None)
    mci(f"close {ALIAS_MCI}", None, 0, None)


def reiniciar_barge_in():
    """Limpia la marca de barge-in. La usa el modo de voz por streaming
    (main._LocutorStreaming) al arrancar un turno nuevo, ya que va a llamar
    a hablar() con reiniciar_cancelacion=False."""
    _cancelado.clear()


def barge_in_pedido():
    """True si alguien pidio cortar la voz (apreto F9 mientras el agente
    hablaba)."""
    return _cancelado.is_set()


def detener_reproduccion():
    """Corta en seco la reproduccion en curso de hablar(), si hay una --
    para barge-in (la persona aprieta F9 mientras el agente todavia esta
    contestando). Segura de llamar aunque no haya nada reproduciendose
    (MCI devuelve un error que se ignora)."""
    _cancelado.set()  # corta tambien si todavia estamos generando el mp3
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f"stop {ALIAS_MCI}", None, 0, None)
    mci(f"close {ALIAS_MCI}", None, 0, None)


def _generar_mp3_edge(texto, ruta_destino):
    """Genera el mp3 con edge-tts. Corre la generacion (asyncio) en un hilo
    nuevo y aislado: si el turno uso preguntar_a_chatgpt (Playwright) antes,
    el hilo del turno puede quedar con el asyncio interno "contaminado" y un
    asyncio.run() directo revienta con RuntimeError -- visto en la practica.
    Un hilo nuevo arranca siempre con asyncio limpio."""

    def _tarea():
        comunicador = edge_tts.Communicate(texto, _voz_tts(), rate=_rate_tts())
        asyncio.run(comunicador.save(str(ruta_destino)))

    hilo = threading.Thread(target=_tarea)
    hilo.start()
    hilo.join()


_piper_voz = None
_piper_lock = threading.Lock()
_piper_no_disponible = False  # se marca si fallo la carga, para no reintentar cada vez


def _cargar_piper():
    """Carga la voz de Piper una sola vez (thread-safe). None si no hay
    modelo o si la carga falla."""
    global _piper_voz, _piper_no_disponible
    if _piper_voz is not None:
        return _piper_voz
    if _piper_no_disponible or _motor_tts() == "edge":
        return None
    with _piper_lock:
        if _piper_voz is not None:
            return _piper_voz
        ruta = _ruta_modelo_piper()
        if ruta is None:
            _piper_no_disponible = True
            if _motor_tts() == "piper":
                _LOG.warning("tts.motor=piper pero no hay modelo en %s -> uso edge-tts", _CARPETA_VOZ_PIPER)
            return None
        try:
            from piper import PiperVoice

            _piper_voz = PiperVoice.load(str(ruta))
            _LOG.info("Voz Piper cargada: %s", ruta.name)
        except Exception:
            _LOG.exception("No se pudo cargar Piper -> uso edge-tts")
            _piper_no_disponible = True
        return _piper_voz


def precargar_tts():
    """Carga la voz de Piper por adelantado (la llama main.py en un hilo al
    arrancar, junto con Whisper y Gemma) para que la primera respuesta no
    pague los ~2s de carga del modelo."""
    _cargar_piper()


def _generar_wav_piper(texto, ruta_destino):
    import wave

    voz = _cargar_piper()
    if voz is None:
        return False
    try:
        from piper import SynthesisConfig

        cfg = SynthesisConfig(length_scale=_piper_length_scale())
        with wave.open(str(ruta_destino), "wb") as wav:
            voz.synthesize_wav(texto, wav, syn_config=cfg)
        return True
    except Exception:
        _LOG.exception("Fallo la sintesis con Piper -> uso edge-tts")
        return False


def _generar_audio(texto):
    """Sintetiza `texto` a un archivo temporal y devuelve su ruta (.wav de
    Piper o .mp3 de edge-tts). El caller lo borra al terminar."""
    usar_piper = _motor_tts() in ("auto", "piper")
    if usar_piper:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            ruta = Path(tmp.name)
        if _generar_wav_piper(texto, ruta):
            return ruta
        ruta.unlink(missing_ok=True)  # cae a edge-tts
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        ruta = Path(tmp.name)
    _generar_mp3_edge(texto, ruta)
    return ruta


def _clave_cache(texto):
    material = f"{_motor_tts()}|{_voz_tts()}|{_rate_tts()}|{_piper_length_scale()}|{texto}".encode()
    return hashlib.sha1(material).hexdigest()


def hablar(texto, reiniciar_cancelacion=True):
    """Convierte `texto` a voz y lo reproduce. Si llega un barge-in
    (detener_reproduccion) mientras se genera o se reproduce el audio, se
    corta y no llega a pisar al usuario.

    Las frases cortas se cachean en disco (privado/tts_cache/): la primera
    vez se sintetizan, las siguientes se reproducen directo -> instantaneo y
    sin internet.

    reiniciar_cancelacion=False: no limpia la marca de barge-in al arrancar.
    Lo usa el modo streaming, que dice varias oraciones seguidas con la misma
    marca -- si cada llamada la limpiara, un barge-in entre oraciones se
    perderia."""
    if reiniciar_cancelacion:
        _cancelado.clear()

    texto = (texto or "").strip()
    if not texto:
        return

    # --- cache de frases cortas ---
    cacheable = len(texto) <= _LARGO_MAX_CACHE
    ruta_cache = None
    if cacheable:
        _CACHE_TTS.mkdir(parents=True, exist_ok=True)
        clave = _clave_cache(texto)
        for ext in (".wav", ".mp3"):
            candidata = _CACHE_TTS / f"{clave}{ext}"
            if candidata.exists():
                if not _cancelado.is_set():
                    _reproducir_audio(candidata)
                return
        ruta_cache = _CACHE_TTS / clave  # sin extension todavia; se define al generar

    ruta_audio = _generar_audio(texto)
    try:
        if _cancelado.is_set():
            return  # el usuario ya empezo a hablar: no pisar con la voz
        if ruta_cache is not None:
            try:
                destino = ruta_cache.with_suffix(ruta_audio.suffix)
                ruta_audio.replace(destino)
                ruta_audio = destino
            except OSError:
                pass  # si no se pudo cachear, se reproduce el temporal igual
        _reproducir_audio(ruta_audio)
    finally:
        if ruta_cache is None or ruta_audio.parent != _CACHE_TTS:
            Path(ruta_audio).unlink(missing_ok=True)
