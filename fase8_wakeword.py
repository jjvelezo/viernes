"""Fase 8 - Wake word: escucha todo el tiempo y dispara cuando detecta que
se dijo "Viernes", después graba y transcribe el comando que sigue.
Standalone: no importa nada de los otros fase*/main.py ni es importado por
ellos. Es el segundo checklist item de la Etapa 1 en
privado/plan_asistente_ia.md.

Sin motor de wake-word dedicado (openWakeWord/Porcupine): esos requieren
entrenar un modelo custom para "Viernes" (no es una palabra en sus catálogos
pre-entrenados) o crear cuenta en un servicio externo. En cambio: un
detector de energía barato (pico de volumen) decide cuándo vale la pena
mirar el audio, y un Whisper 'base' (rápido, corre todo el rato) revisa si
lo que se dijo se parece a "viernes" (matching difuso, no exacto — un
modelo chico transcribiendo una sola palabra suelta, sin contexto, la
garabatea seguido: "vierna", "biennes", "piernas"... son transcripciones
típicas de alguien diciendo "viernes" de verdad) antes de prender el modelo
'small' (más preciso, el mismo de fase7_voz.py) recién para transcribir el
comando real.

Todavía no ejecuta nada — solo imprime el comando reconocido. Conectarlo con
fase6_asistente.py (abrir_app, preguntar_chatgpt, etc.) es el paso
siguiente."""

import difflib
import sys
import unicodedata

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

MUESTREO_HZ = 16000
VENTANA_ESCUCHA_S = 2.0  # cuánto audio se analiza por chequeo mientras espera la wake word
COLA_SOLAPE_S = 0.5  # se arrastra este tramo del final de la ventana anterior, para no cortar "viernes" al medio
PICO_MINIMO_VOZ = 0.02  # por debajo de esto se asume silencio y ni se transcribe (ahorra CPU)
MODELO_WAKE = "base"  # corre todo el tiempo escuchando; "tiny" resultó demasiado impreciso para una palabra suelta
MODELO_COMANDO = "small"  # preciso, solo se usa después de detectar la wake word (igual que fase7_voz.py)
PALABRA_CLAVE = "viernes"
SIMILITUD_MINIMA = 0.65  # calibrado contra mishears reales: "vierna"/"biennes"/"piernas" ~0.71-0.77, ruido tipo "hola"/"pero" ~0.0-0.44
SEGUNDOS_COMANDO = 5


def _normalizar(texto):
    """Minúsculas, sin tildes y sin puntuación, para no perder "viernes"
    por cosas tipo "¡Viérnes?" o transcripciones ligeramente distintas."""
    texto = unicodedata.normalize("NFKD", texto.lower().strip())
    sin_tildes = "".join(c for c in texto if not unicodedata.combining(c))
    return "".join(c for c in sin_tildes if c.isalnum() or c.isspace())


def _contiene_palabra_clave(texto):
    """No busca "viernes" exacto: un modelo chico transcribiendo una
    palabra suelta sin contexto la garabatea seguido (ver docstring del
    módulo), así que compara cada palabra transcripta contra "viernes" con
    difflib y acepta si se parece lo suficiente (SIMILITUD_MINIMA)."""
    palabras = _normalizar(texto).split()
    return any(
        difflib.SequenceMatcher(None, PALABRA_CLAVE, palabra).ratio() >= SIMILITUD_MINIMA
        for palabra in palabras
    )


def _grabar(segundos):
    audio = sd.rec(int(segundos * MUESTREO_HZ), samplerate=MUESTREO_HZ, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def escuchar_wake_word(modelo_wake):
    """Bloquea hasta escuchar "Viernes". Graba en ventanas de
    VENTANA_ESCUCHA_S segundos, arrastrando un poco de la anterior para no
    perder la palabra si queda justo en el corte."""
    print(f'  Escuchando... decí "{PALABRA_CLAVE}" para activar (Ctrl+C para salir).')
    cola = np.zeros(0, dtype="float32")
    muestras_cola = int(COLA_SOLAPE_S * MUESTREO_HZ)

    while True:
        nuevo = _grabar(VENTANA_ESCUCHA_S)
        pico = float(np.abs(nuevo).max())
        if pico < PICO_MINIMO_VOZ:
            print(f"    (silencio, pico={pico:.4f})")
            cola = nuevo[-muestras_cola:]
            continue

        audio = np.concatenate([cola, nuevo])
        cola = nuevo[-muestras_cola:]

        segmentos, _info = modelo_wake.transcribe(audio, language="es")
        texto = " ".join(segmento.text for segmento in segmentos)
        print(f'    (pico={pico:.4f}) escuché: "{texto.strip()}"')
        if _contiene_palabra_clave(texto):
            return


def escuchar_comando(modelo_comando):
    print(f"  ¡Te escucho! Decí el comando ({SEGUNDOS_COMANDO}s)...")
    audio = _grabar(SEGUNDOS_COMANDO)
    segmentos, _info = modelo_comando.transcribe(audio, language="es")
    return " ".join(segmento.text.strip() for segmento in segmentos)


def bucle_principal():
    print('=== Fase 8: wake word "Viernes" ===')
    print(f"  Cargando modelo '{MODELO_WAKE}' (siempre activo)...")
    modelo_wake = WhisperModel(MODELO_WAKE, device="cpu", compute_type="int8")
    print(f"  Cargando modelo '{MODELO_COMANDO}' (para el comando, después de la wake word)...")
    modelo_comando = WhisperModel(MODELO_COMANDO, device="cpu", compute_type="int8")
    print("  Listo.\n")

    while True:
        escuchar_wake_word(modelo_wake)
        print("  ¡Wake word detectada!")
        texto = escuchar_comando(modelo_comando)
        if texto:
            print(f'  Comando escuchado: "{texto}"\n')
        else:
            print("  No entendí nada después de la wake word.\n")
        # TODO: acá se conecta con fase6_asistente.py (un intents.py que
        # matchee `texto` contra abrir_app/preguntar_chatgpt/volumen/etc.)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("El resto del proyecto asume Windows.", file=sys.stderr)
    try:
        bucle_principal()
    except KeyboardInterrupt:
        print("\n  Cortado.")
