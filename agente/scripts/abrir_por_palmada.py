#!/usr/bin/env python3
"""Abrir Viernes con doble palmada.

Escucha el microfono. Al detectar dos palmadas seguidas (entre
min_gap_seconds y max_gap_seconds) lanza el agente (agente/main.py) y
SE CIERRA -- no queda escuchando. A partir de ahi Viernes se usa normal
con F9 y se cierra desde el icono de la bandeja.

Portado de jarvis_template/scripts/clap-trigger.py (misma deteccion:
RMS por bloque, dos picos dentro de una ventana de tiempo). Los
parametros salen del bloque "palmada" de config.json -- los valores por
defecto son los que ya funcionaban en jarvis_template.

Por defecto solo escucha durante los primeros `window_minutes` desde que
se prendio la PC (pensado para lanzarse al inicio de sesion de Windows).
  --force  : ignora esa ventana y escucha hasta que aplaudas o Ctrl+C.
  --debug  : imprime el RMS en vivo para calibrar `threshold`.
"""

import argparse
import subprocess
import sys
import time
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

SAMPLE_RATE = config.obtener("palmada.sample_rate", 44100)
BLOCK_SIZE = config.obtener("palmada.block_size", 1024)
THRESHOLD = config.obtener("palmada.threshold", 0.07)
MIN_GAP = config.obtener("palmada.min_gap_seconds", 0.15)
MAX_GAP = config.obtener("palmada.max_gap_seconds", 0.8)
WINDOW_SEGUNDOS = config.obtener("palmada.window_minutes", 5) * 60


def _viernes_ya_corriendo():
    """True si ya hay un proceso corriendo agente/main.py -- para no
    abrir dos Viernes si aplaudis con uno ya abierto."""
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


def _segundos_a_escuchar(forzar):
    if forzar:
        print("[palmada] --force: escuchando hasta que aplaudas (Ctrl+C para cortar).")
        return float("inf")
    encendida_hace = time.time() - psutil.boot_time()
    restante = WINDOW_SEGUNDOS - encendida_hace
    if restante <= 0:
        print(f"[palmada] La PC lleva prendida mas de {WINDOW_SEGUNDOS / 60:.0f} min. Salgo.")
        sys.exit(0)
    print(f"[palmada] Escuchando la doble palmada por {restante:.0f}s.")
    return restante


def main():
    parser = argparse.ArgumentParser(description="Abrir Viernes con doble palmada")
    parser.add_argument("--force", action="store_true", help="Ignorar la ventana desde el arranque")
    parser.add_argument("--debug", action="store_true", help="Imprimir RMS para calibrar threshold")
    args = parser.parse_args()

    if _viernes_ya_corriendo():
        print("[palmada] Viernes ya esta abierto. Salgo.")
        return

    limite = time.time() + _segundos_a_escuchar(args.force)

    disparado = [False]
    ultima_palmada = [0.0]

    def callback(indata, _frames, _time_info, _status):
        ahora = time.time()
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if args.debug and rms > 0.01:
            print(f"[debug] rms={rms:.4f}", flush=True)
        if rms <= THRESHOLD:
            return
        gap = ahora - ultima_palmada[0]
        if gap < MIN_GAP:
            return  # el "rebote" de la misma palmada
        if ultima_palmada[0] > 0 and gap <= MAX_GAP:
            print("[palmada] Doble palmada. Abriendo Viernes...", flush=True)
            ultima_palmada[0] = 0.0
            disparado[0] = True
            _lanzar_viernes()
        else:
            ultima_palmada[0] = ahora

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
            dtype="float32", callback=callback,
        ):
            while time.time() < limite and not disparado[0]:
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    print("[palmada] " + ("Viernes lanzado. Salgo." if disparado[0] else "Se cerro la ventana sin palmada. Salgo."))


if __name__ == "__main__":
    main()
