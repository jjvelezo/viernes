#!/usr/bin/env python3
"""Abrir Viernes con una tecla + doble palmada.

Proceso liviano que se deja corriendo (ponerlo en el inicio de sesion de
Windows). En reposo NO escucha el microfono: solo tiene enganchado un
atajo de teclado global.

  - Apretas la tecla (`palmada.tecla`, pensada para mapearla a una tecla
    fisica con G-Helper)  ->  abre el microfono y escucha la doble palmada
    (icono azul en la bandeja).
  - Doble palmada  ->  lanza agente/main.py y vuelve a reposo. A
    partir de ahi Viernes se usa normal con F9 y se cierra desde su propio
    icono.
  - Apretas la tecla de nuevo mientras escucha  ->  cancela (por si la
    apretaste sin querer).
  - Si en `palmada.escucha_segundos` no hubo palmada, deja de escuchar
    solo.
  - "Salir" en el icono de la bandeja cierra este proceso.

Deteccion de palmada portada de jarvis_template/scripts/clap-trigger.py
(RMS por bloque, dos picos entre min_gap y max_gap). Parametros en el
bloque "palmada" de config.json.
"""

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import keyboard
import numpy as np
import psutil
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

RAIZ_AGENTE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_AGENTE))
import config  # noqa: E402

SCRIPT_AGENTE = RAIZ_AGENTE / "main.py"
_VENV = RAIZ_AGENTE.parent / "venv" / "Scripts"
PYTHON_SIN_CONSOLA = _VENV / "pythonw.exe"
PYTHON_VENV = _VENV / "python.exe"

TECLA = config.obtener("palmada.tecla", "ctrl+alt+f12")
ESCUCHA_SEGUNDOS = config.obtener("palmada.escucha_segundos", 120)
SAMPLE_RATE = config.obtener("palmada.sample_rate", 44100)
BLOCK_SIZE = config.obtener("palmada.block_size", 1024)
THRESHOLD = config.obtener("palmada.threshold", 0.07)
MIN_GAP = config.obtener("palmada.min_gap_seconds", 0.15)
MAX_GAP = config.obtener("palmada.max_gap_seconds", 0.8)


def _icono(color):
    imagen = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).ellipse((8, 8, 56, 56), fill=color)
    return imagen


ICONO_REPOSO = _icono((128, 128, 128, 255))     # gris: no escucha nada
ICONO_ESCUCHANDO = _icono((30, 144, 255, 255))  # azul: esperando la doble palmada


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


class Escucha:
    """Maneja el ciclo abrir-microfono / detectar-palmada / cerrar."""

    def __init__(self, icono):
        self._icono = icono
        self._lock = threading.Lock()
        self._stream = None
        self._ultima_palmada = 0.0
        self._timer = None

    @property
    def activa(self):
        return self._stream is not None

    def toggle(self):
        with self._lock:
            if self.activa:
                self._parar("cancelado con la tecla")
            else:
                self._arrancar()

    def _arrancar(self):
        if _viernes_ya_corriendo():
            print("[palmada] Viernes ya esta abierto.")
            return
        self._ultima_palmada = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()
        self._icono.icon = ICONO_ESCUCHANDO
        self._timer = threading.Timer(ESCUCHA_SEGUNDOS, self._parar_por_timeout)
        self._timer.start()
        print(f"[palmada] Escuchando la doble palmada ({ESCUCHA_SEGUNDOS}s).")

    def _parar_por_timeout(self):
        with self._lock:
            if self.activa:
                self._parar("sin palmada en el tiempo previsto")

    def _parar(self, motivo):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._icono.icon = ICONO_REPOSO
        print(f"[palmada] Dejo de escuchar ({motivo}).")

    def _callback(self, indata, _frames, _time_info, _status):
        ahora = time.time()
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms <= THRESHOLD:
            return
        gap = ahora - self._ultima_palmada
        if gap < MIN_GAP:
            return  # rebote de la misma palmada
        if self._ultima_palmada > 0 and gap <= MAX_GAP:
            print("[palmada] Doble palmada. Abriendo Viernes...")
            self._ultima_palmada = 0.0
            _lanzar_viernes()
            # No se puede tomar self._lock aca (el callback corre en el
            # hilo de sounddevice); se para en un hilo aparte.
            threading.Thread(target=self._parar_tras_disparo, daemon=True).start()
        else:
            self._ultima_palmada = ahora

    def _parar_tras_disparo(self):
        with self._lock:
            if self.activa:
                self._parar("Viernes lanzado")


def setup(icono):
    icono.visible = True
    escucha = Escucha(icono)
    keyboard.add_hotkey(TECLA, lambda: threading.Thread(target=escucha.toggle, daemon=True).start())
    print(f'[palmada] Listo. Apreta "{TECLA}" y despues aplaudi dos veces para abrir Viernes.')

    def salir(_icono, _item):
        keyboard.unhook_all()
        with escucha._lock:
            if escucha.activa:
                escucha._parar("saliendo")
        icono.stop()

    icono.menu = pystray.Menu(pystray.MenuItem("Salir", salir))


def _calibrar():
    """Imprime el RMS en vivo para elegir `palmada.threshold`. Aplaudi
    varias veces, habla normal, tose: el umbral va entre el pico de la voz
    y el de las palmadas. Ctrl+C para cortar."""
    print(f"[calibrar] threshold actual = {THRESHOLD}. Aplaudi / habla / tose. Ctrl+C para salir.")

    def cb(indata, _f, _t, _s):
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms > 0.01:
            marca = "  <-- PASA el umbral" if rms > THRESHOLD else ""
            print(f"rms={rms:.4f}{marca}")

    with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
                        dtype="float32", callback=cb):
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass


def main():
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    parser = argparse.ArgumentParser(description="Abrir Viernes con tecla + doble palmada")
    parser.add_argument("--debug", action="store_true", help="Imprimir RMS para calibrar palmada.threshold")
    if parser.parse_args().debug:
        _calibrar()
        return
    icono = pystray.Icon("abrir_por_palmada", icon=ICONO_REPOSO, title="Abrir Viernes (palmada)")
    icono.run(setup=setup)


if __name__ == "__main__":
    main()
