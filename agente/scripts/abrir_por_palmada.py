#!/usr/bin/env python3
"""Abrir Viernes con una combinacion de teclas + doble palmada.

Se deja corriendo (va al inicio de sesion de Windows). En reposo lo unico
activo es el enganche del teclado: el MICROFONO NO se abre.

  1. Apretas `palmada.tecla` (por defecto Ctrl+Shift+F8) desde cualquier lado.
  2. Se abre el microfono y escucha unos segundos (cartel azul abajo a la
     derecha).
  3. Doble palmada  ->  lanza agente/main.py, cartel verde, y vuelve a
     reposo (el micro se cierra).
  4. Si no hay palmada en `palmada.escucha_segundos`, se cierra el micro
     solo (cartel gris) y vuelve a reposo.

Sigue corriendo para la proxima vez. Para cerrarlo del todo: Administrador
de tareas -> pythonw.exe de este script (o cerrar sesion de Windows).

Nota Windows: si la ventana con foco corre como administrador y este
proceso no, el hook de teclado puede no recibir la combinacion (UIPI, no
es un bug de aca) -- pasa lo mismo con el F9 de Viernes.

Deteccion de palmada portada de jarvis_template/scripts/clap-trigger.py:
RMS por bloque de audio, dos picos sobre `threshold` separados entre
`min_gap_seconds` y `max_gap_seconds`. Parametros en el bloque "palmada"
de config.json (los de jarvis_template ya vienen calibrados).
"""

import queue
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

import keyboard
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

TECLA = config.obtener("palmada.tecla", "ctrl+shift+f8")
ESCUCHA_SEGUNDOS = config.obtener("palmada.escucha_segundos", 120)
SAMPLE_RATE = config.obtener("palmada.sample_rate", 44100)
BLOCK_SIZE = config.obtener("palmada.block_size", 1024)
THRESHOLD = config.obtener("palmada.threshold", 0.07)
MIN_GAP = config.obtener("palmada.min_gap_seconds", 0.15)
MAX_GAP = config.obtener("palmada.max_gap_seconds", 0.8)

COLOR_ESCUCHANDO = "#1E5FA8"  # azul
COLOR_LISTO = "#2E7D32"       # verde
COLOR_NADA = "#555555"        # gris


class _Aviso:
    """Cartelito sin bordes, arriba de todo, abajo a la derecha. Se
    muestra unos ms y se esconde solo."""

    def __init__(self, root):
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        self._label = tk.Label(root, font=("Segoe UI", 12), fg="white", padx=22, pady=12)
        self._label.pack()
        root.withdraw()

    def mostrar(self, texto, color, ms=1600):
        self._label.configure(text=texto, bg=color)
        self._root.configure(bg=color)
        self._root.update_idletasks()
        ancho = self._root.winfo_reqwidth()
        alto = self._root.winfo_reqheight()
        x = self._root.winfo_screenwidth() - ancho - 28
        y = self._root.winfo_screenheight() - alto - 70
        self._root.geometry(f"+{x}+{y}")
        self._root.deiconify()
        self._root.after(ms, self._root.withdraw)


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


class Controlador:
    """Maquina de estados: reposo <-> escuchando palmadas. Corre sobre el
    mainloop de Tk (poll cada 100ms); el hook de teclado y el callback de
    audio solo dejan senales en variables/cola, no tocan Tk."""

    def __init__(self, root, aviso):
        self._root = root
        self._aviso = aviso
        self._pedidos = queue.Queue()
        self._stream = None
        self._limite = 0.0
        self._ultima_palmada = 0.0
        self._doble = False

    def pedir_escucha(self):
        self._pedidos.put(1)  # lo llama el hook de teclado

    def _audio(self, indata, _frames, _time_info, _status):
        ahora = time.time()
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms <= THRESHOLD:
            return
        gap = ahora - self._ultima_palmada
        if gap < MIN_GAP:
            return
        if self._ultima_palmada > 0 and gap <= MAX_GAP:
            self._ultima_palmada = 0.0
            self._doble = True
        else:
            self._ultima_palmada = ahora

    def _arrancar(self):
        if self._stream is not None:
            return  # ya estaba escuchando
        if _viernes_ya_corriendo():
            self._aviso.mostrar("Viernes ya esta abierto", COLOR_NADA, ms=1400)
            return
        self._ultima_palmada = 0.0
        self._doble = False
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1,
            dtype="float32", callback=self._audio,
        )
        self._stream.start()
        self._limite = time.time() + ESCUCHA_SEGUNDOS
        self._aviso.mostrar("Escuchando palmadas...", COLOR_ESCUCHANDO)
        print("[palmada] Escuchando.")

    def _parar(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def tick(self):
        try:
            while True:
                self._pedidos.get_nowait()
                self._arrancar()
        except queue.Empty:
            pass

        if self._stream is not None:
            if self._doble:
                self._parar()
                self._aviso.mostrar("Abriendo Viernes", COLOR_LISTO, ms=1500)
                _lanzar_viernes()
                print("[palmada] Viernes lanzado.")
            elif time.time() >= self._limite:
                self._parar()
                self._aviso.mostrar("Sin palmada", COLOR_NADA, ms=1200)
                print("[palmada] Timeout, vuelvo a reposo.")

        self._root.after(100, self.tick)


def main():
    root = tk.Tk()
    ctrl = Controlador(root, _Aviso(root))
    keyboard.add_hotkey(TECLA, ctrl.pedir_escucha)
    print(f'[palmada] En reposo. Apreta "{TECLA}" y despues aplaudi dos veces.')
    root.after(100, ctrl.tick)
    root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este proyecto asume Windows.", file=sys.stderr)
    main()
