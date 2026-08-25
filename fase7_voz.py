"""Fase 7 - Voz: graba unos segundos del micrófono real y los transcribe con
faster-whisper, para medir latencia antes de meter wake word (paso 1 de la
Etapa 1 en privado/plan_asistente_ia.md). Standalone: no importa nada de los
otros fase*/main.py ni es importado por ellos.

Requiere hablarle de verdad al micrófono, así que el menú es interactivo —
no se puede probar sin un humano al lado."""

import sys
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

MUESTREO_HZ = 16000
MODELO = "small"  # si tarda demasiado en esta máquina (CPU, sin GPU para esto), probar "base"
PICO_MINIMO_AUDIBLE = 0.01  # por debajo de esto, casi seguro no se captó voz real


def grabar(segundos):
    print(f"  Grabando {segundos}s...")
    audio = sd.rec(int(segundos * MUESTREO_HZ), samplerate=MUESTREO_HZ, channels=1, dtype="float32")
    sd.wait()
    audio = audio.flatten()
    pico = float(np.abs(audio).max()) if audio.size else 0.0
    print(f"  Listo. Pico de volumen captado: {pico:.4f}")
    if pico < PICO_MINIMO_AUDIBLE:
        print(
            "  ⚠ Eso es prácticamente silencio: seguramente se grabó del micrófono "
            "equivocado (o está muteado). Revisá el dispositivo de entrada por defecto de Windows."
        )
    return audio


def transcribir(modelo, audio):
    inicio = time.monotonic()
    segmentos, _info = modelo.transcribe(audio, language="es")
    texto = " ".join(segmento.text.strip() for segmento in segmentos)
    duracion = time.monotonic() - inicio
    return texto, duracion


def pedir_segundos(mensaje, valor_por_defecto):
    entrada = input(mensaje).strip()
    if not entrada:
        return valor_por_defecto
    try:
        return int(entrada)
    except ValueError:
        print(f"  Eso no es un número, uso {valor_por_defecto}s.")
        return valor_por_defecto


def menu():
    print("=== Fase 7: voz (faster-whisper, prueba de micrófono real) ===")
    dispositivo_entrada = sd.query_devices(sd.default.device[0])
    print(f"  Micrófono por defecto: \"{dispositivo_entrada['name']}\"")
    print(f"  Cargando modelo '{MODELO}' (CPU, int8)...")
    inicio_carga = time.monotonic()
    modelo = WhisperModel(MODELO, device="cpu", compute_type="int8")
    print(f"  Modelo cargado en {time.monotonic() - inicio_carga:.1f}s.\n")

    while True:
        segundos = pedir_segundos("  ¿Cuántos segundos grabar? (enter = 5): ", 5)
        for cuenta in (3, 2, 1):
            print(f"    {cuenta}...")
            time.sleep(1)

        audio = grabar(segundos)
        texto, duracion = transcribir(modelo, audio)

        if texto:
            print(f'\n  Transcripción: "{texto}"')
        else:
            print("\n  Transcripción vacía (Whisper no reconoció nada en ese audio).")
        print(f"  Tardó {duracion:.2f}s en transcribir {segundos}s de audio.\n")

        respuesta = input("¿Probar de nuevo? (s/n): ").strip().lower()
        if respuesta != "s":
            break


if __name__ == "__main__":
    if sys.platform != "win32":
        print("El resto del proyecto asume Windows; esto debería andar en otros SO igual, pero no está probado.", file=sys.stderr)
    menu()
