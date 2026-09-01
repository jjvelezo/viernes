"""Skill: control por gestos con la mano ("manos libres").

Viernes es la IA; el mouse gestual (carpeta mouse_gestual/ en la raiz del
repo) es una capacidad mas que se enciende y se apaga por voz. Se lanza
como un proceso APARTE a proposito -- misma regla de aislamiento que el
resto de agente/: nada de mouse_gestual/ se importa aca, solo se ejecuta
con el Python del venv. Si el mouse gestual se cuelga o crashea, no se
lleva puesto al agente.

La ventana de OpenCV del mouse gestual se puede cerrar tambien a mano con
'q'; desactivar_manos_libres() hace lo mismo matando el proceso."""

import atexit
import subprocess
import sys
from pathlib import Path

RAIZ_VIERNES = Path(__file__).resolve().parent.parent.parent
_SCRIPT = RAIZ_VIERNES / "mouse_gestual" / "main.py"
_PYTHON_VENV = RAIZ_VIERNES / "venv" / "Scripts" / "python.exe"

_proceso = None


def _vivo():
    return _proceso is not None and _proceso.poll() is None


def activar_manos_libres() -> str:
    """Activa el control del cursor y las ventanas con gestos de la mano
    frente a la webcam. Usar cuando el usuario pida "manos libres",
    "controlar con la mano", "activar los gestos" o algo asi."""
    global _proceso
    if _vivo():
        return "El control por gestos ya estaba activo."
    if not _SCRIPT.exists():
        raise ValueError("No encontre main.py del mouse gestual.")
    ejecutable = str(_PYTHON_VENV) if _PYTHON_VENV.exists() else sys.executable
    _proceso = subprocess.Popen([ejecutable, str(_SCRIPT)], cwd=str(_SCRIPT.parent))
    return "Listo, control por gestos activado. Mové la mano frente a la cámara."


def desactivar_manos_libres() -> str:
    """Desactiva el control por gestos y cierra la ventana de la camara.
    Usar cuando el usuario diga "desactivar manos libres", "apagá los
    gestos", "ya no uses la cámara" o algo asi."""
    global _proceso
    if not _vivo():
        return "El control por gestos no estaba activo."
    _proceso.terminate()
    try:
        _proceso.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _proceso.kill()
    _proceso = None
    return "Control por gestos desactivado."


@atexit.register
def _cerrar_al_salir():
    if _vivo():
        _proceso.terminate()


TOOLS = [activar_manos_libres, desactivar_manos_libres]
