"""Primitivas de audio del agente base: grabacion push-to-talk, STT y TTS.

Portado (no importado) de fase10_asistente_ptt.py y fase6_asistente.py del
proyecto Viernes -- mismo patron ya validado ahi (sd.InputStream +
callback, WhisperModel "small" en CPU, edge-tts + MCI para la respuesta
hablada). Se copia en vez de importarse para que agente/ se pueda mover a
su propia carpeta o repo despues sin arrastrar el resto de Viernes."""

import asyncio
import ctypes
import tempfile
import threading
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

VOZ_TTS = config.obtener("tts.voice", "es-MX-DaliaNeural")  # historial de voces: ver fase6_asistente.py
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
    estado.grabando este activo (igual que en fase10_asistente_ptt.py)."""
    stream = sd.InputStream(
        samplerate=MUESTREO_HZ, channels=1, dtype="float32", callback=estado.callback_audio
    )
    stream.start()
    return stream


def cargar_modelo_stt():
    return WhisperModel(MODELO_STT, device="cpu", compute_type="int8")


def transcribir(audio, modelo):
    """Devuelve el texto transcripto, o "" si el audio es muy corto
    (toque sin querer) o no se entendio nada."""
    if audio.size < MUESTREO_HZ * 0.3:
        return ""
    segmentos, _info = modelo.transcribe(audio, language="es", initial_prompt=PROMPT_INICIAL)
    return " ".join(segmento.text.strip() for segmento in segmentos).strip()


ALIAS_MCI = "voz_agente"


def _reproducir_mp3(ruta):
    """Reproduce un mp3 con el driver MCI de Windows -- sin dependencias
    nuevas ademas de edge-tts, que no sabe decodificar/reproducir audio.
    "play ... wait" bloquea hasta que termine SOLO o hasta que otro hilo
    llame detener_reproduccion() (barge-in) -- ver main.py."""
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f'open "{ruta}" type mpegvideo alias {ALIAS_MCI}', None, 0, None)
    mci(f"play {ALIAS_MCI} wait", None, 0, None)
    mci(f"close {ALIAS_MCI}", None, 0, None)


def detener_reproduccion():
    """Corta en seco la reproduccion en curso de hablar(), si hay una --
    para barge-in (la persona aprieta F9 mientras el agente todavia esta
    contestando). Segura de llamar aunque no haya nada reproduciendose
    (MCI devuelve un error que se ignora)."""
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
    temporal y lo borra al terminar."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
        ruta_temp = Path(temporal.name)
    try:
        _generar_mp3(texto, ruta_temp)
        _reproducir_mp3(ruta_temp)
    finally:
        ruta_temp.unlink(missing_ok=True)
