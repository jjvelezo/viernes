#!/usr/bin/env python3
"""Abrir Viernes con doble palmada.

No queda nada en reposo: este script se LANZA con un atajo de teclado
(ver README -- un acceso directo con "tecla de metodo abreviado", o una
tecla mapeada en G-Helper a ese mismo atajo). Al arrancar abre el
microfono y escucha; con dos palmadas seguidas lanza agente/main.py y se
cierra. Desde ahi Viernes se usa normal con F9 y se cierra desde su icono
en la bandeja.

Si en `palmada.escucha_segundos` no hubo palmada (lo apretaste sin
querer), el script se cierra solo.

Deteccion portada de jarvis_template/scripts/clap-trigger.py: RMS por
bloque de audio, dos picos por encima de `threshold` separados entre
`min_gap_seconds` y `max_gap_seconds`. Parametros en el bloque "palmada"
de config.json (los de jarvis_template ya vienen bien calibrados).
"""

import subprocess
import sys
import time
import winsound
from pathlib import Path

import numpy as np
import psutil
import sounddevice as sd

RAIZ_AGENTE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_AGENTE))
import config  # noqa: E402

SCRIPT_AGENTE = RAIZ_AGENTE / "main.py"
_VENV = RAIZ_AGENTE.parent / "venv" / "Scripts"
PYTHON_SIN_CONSOLA = _VENV / "pythonw.exe"
PYTHON_VENV = _VENV / "python.exe"

ESCUCHA_SEGUNDOS = config.obtener("palmada.escucha_segundos", 120)
SAMPLE_RATE = config.obtener("palmada.sample_rate", 44100)
BLOCK_SIZE = config.obtener("palmada.block_size", 1024)
THRESHOLD = config.obtener("palmada.threshold", 0.07)
MIN_GAP = config.obtener("palmada.min_gap_seconds", 0.15)
MAX_GAP = config.obtener("palmada.max_gap_seconds", 0.8)


def _bip(secuencia):
    """Feedback audible (no hay ventana ni icono). secuencia: lista de
    (frecuencia_hz, duracion_ms)."""
    try:
        for freq, dur in secuencia:
            winsound.Beep(freq, dur)
    except RuntimeError:
        pass  # algunas placas no soportan Beep, no es critico


BIP_ESCUCHANDO = [(880, 120), (1320, 120)]        # sube: "te escucho"
BIP_DISPARADO = [(1320, 90), (1760, 90), (2093, 160)]  # sube mas: "listo, abriendo"
BIP_TIMEOUT = [(440, 250)]                          # grave: "me apago sin nada"


def _viernes_ya_corriendo():
    objetivo = str(SCRIPT_AGENTE).lower()
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if objetivo in cmd or ("agente" in cmd and "main.py" in cmd):
            return True
    return False


def _lanzar_viernes():
    ejecutable = PYTHON_SIN_CONSOLA if PYTHON_SIN_CONSOLA.exists() else (
        PYTHON_VENV if PYTHON_VENV.exists() else Path(sys.executable)
    )
    subprocess.Popen(
        [str(ejecutable), str(SCRIPT_AGENTE)],
        cwd=str(RAIZ_AGENTE),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main():
    if _viernes_ya_corriendo():
        print("[palmada] Viernes ya esta abierto. Salgo.")
        return

    limite = time.time() + ESCUCHA_SEGUNDOS
    disparado = [False]
    ultima_palmada = [0.0]

    def callback(indata, _frames, _time_info, _status):
        ahora = time.time()
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms <= THRESHOLD:
            return
        gap = ahora - ultima_palmada[0]
        if gap < MIN_GAP:
            return  # rebote de la misma palmada
        if ultima_palmada[0] > 0 and gap <= MAX_GAP:
            print("[palmada] Doble palmada. Abriendo Viernes...")
            ultima_palmada[0] = 0.0
            disparado[0] = True
        else:
            ultima_palmada[0] = ahora

    print(f"[palmada] Escuchando la doble palmada ({ESCUCHA_SEGUNDOS}s).")
    _bip(BIP_ESCUCHANDO)
    with sd.InputStream(
        samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
        dtype="float32", callback=callback,
    ):
        while time.time() < limite and not disparado[0]:
            time.sleep(0.1)

    if disparado[0]:
        _bip(BIP_DISPARADO)
        _lanzar_viernes()
        print("[palmada] Viernes lanzado. Salgo.")
    else:
        _bip(BIP_TIMEOUT)
        print("[palmada] Sin palmada, salgo.")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    main()
