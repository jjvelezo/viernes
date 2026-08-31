#!/usr/bin/env python3
"""Abrir Viernes con doble palmada.

No queda nada en reposo: este script se LANZA con un atajo de teclado
(ver README -- un acceso directo con "tecla de metodo abreviado", o una
tecla mapeada en G-Helper a ese mismo atajo). Al arrancar abre el
microfono y escucha; con dos palmadas seguidas lanza agente/main.py y se
cierra. Desde ahi Viernes se usa normal con F9 y se cierra desde su icono
en la bandeja.

Como no tiene ventana propia ni icono, avisa con un cartelito breve
abajo a la derecha (ver _Aviso): al empezar a escuchar, al detectar la
doble palmada, y si se cierra sin palmada tras `palmada.escucha_segundos`.

Deteccion portada de jarvis_template/scripts/clap-trigger.py: RMS por
bloque de audio, dos picos por encima de `threshold` separados entre
`min_gap_seconds` y `max_gap_seconds`. Parametros en el bloque "palmada"
de config.json (los de jarvis_template ya vienen bien calibrados).
"""

import subprocess
import sys
import time
import tkinter as tk
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


class _Aviso:
    """Cartelito sin bordes, arriba de todo, abajo a la derecha de la
    pantalla principal. Se muestra unos ms y se esconde solo."""

    def __init__(self, root):
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        self._label = tk.Label(
            root, font=("Segoe UI", 12), fg="white", padx=22, pady=12, bg="#1E5FA8"
        )
        self._label.pack()
        root.withdraw()

    def mostrar(self, texto, color, ms=1600):
        self._label.configure(text=texto, bg=color)
        self._root.configure(bg=color)
        self._root.update_idletasks()
        ancho = self._root.winfo_reqwidth()
        alto = self._root.winfo_reqheight()
        pantalla_ancho = self._root.winfo_screenwidth()
        pantalla_alto = self._root.winfo_screenheight()
        x = pantalla_ancho - ancho - 28
        y = pantalla_alto - alto - 70
        self._root.geometry(f"+{x}+{y}")
        self._root.deiconify()
        self._root.after(ms, self._root.withdraw)


COLOR_ESCUCHANDO = "#1E5FA8"  # azul
COLOR_LISTO = "#2E7D32"       # verde
COLOR_NADA = "#555555"        # gris


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
        # Corre en el hilo de sounddevice: solo toca variables locales,
        # nada de Tk (eso lo maneja el poller en el hilo principal).
        ahora = time.time()
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms <= THRESHOLD:
            return
        gap = ahora - ultima_palmada[0]
        if gap < MIN_GAP:
            return  # rebote de la misma palmada
        if ultima_palmada[0] > 0 and gap <= MAX_GAP:
            print("[palmada] Doble palmada.")
            ultima_palmada[0] = 0.0
            disparado[0] = True
        else:
            ultima_palmada[0] = ahora

    root = tk.Tk()
    aviso = _Aviso(root)

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
        dtype="float32", callback=callback,
    )
    stream.start()
    print(f"[palmada] Escuchando la doble palmada ({ESCUCHA_SEGUNDOS}s).")
    aviso.mostrar("Escuchando palmadas...", COLOR_ESCUCHANDO)

    def poll():
        if disparado[0]:
            _cerrar(True)
        elif time.time() >= limite:
            _cerrar(False)
        else:
            root.after(150, poll)

    def _cerrar(ok):
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        if ok:
            aviso.mostrar("Abriendo Viernes", COLOR_LISTO, ms=1500)
            _lanzar_viernes()
            print("[palmada] Viernes lanzado. Salgo.")
        else:
            aviso.mostrar("Sin palmada", COLOR_NADA, ms=1200)
            print("[palmada] Sin palmada, salgo.")
        root.after(1700 if ok else 1400, root.destroy)

    root.after(150, poll)
    root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    main()
