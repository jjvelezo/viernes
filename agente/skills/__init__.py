"""Registro de skills: junta las tools de cada modulo en una sola lista
para pasarle al motor LLM (ver core/motor_llm.py).

`activas()` devuelve solo las tools de las skills encendidas en
`personalizacion.skills` de config.json (lo toca el panel de la ventana de
chat). Se llama por turno -- leer config es barato. `TOOLS` se mantiene como
la lista completa por compatibilidad."""

import config

from .apps import TOOLS as _TOOLS_APPS
from .carpetas import TOOLS as _TOOLS_CARPETAS
from .chatgpt import TOOLS as _TOOLS_CHATGPT
from .internet import TOOLS as _TOOLS_INTERNET
from .manos_libres import TOOLS as _TOOLS_MANOS_LIBRES
from .rutina import TOOLS as _TOOLS_RUTINA
from .spotify import TOOLS as _TOOLS_SPOTIFY
from .tiempo import TOOLS as _TOOLS_TIEMPO
from .ventanas import TOOLS as _TOOLS_VENTANAS
from .volumen import TOOLS as _TOOLS_VOLUMEN

# nombre interno -> (etiqueta para el panel, lista de tools). El orden es el
# que ve el modelo.
_REGISTRO = {
    "apps": ("Abrir aplicaciones", _TOOLS_APPS),
    "volumen": ("Control de volumen", _TOOLS_VOLUMEN),
    "internet": ("Buscar en internet", _TOOLS_INTERNET),
    "chatgpt": ("Preguntar a ChatGPT", _TOOLS_CHATGPT),
    "tiempo": ("Fecha y hora", _TOOLS_TIEMPO),
    "carpetas": ("Abrir carpetas y proyectos", _TOOLS_CARPETAS),
    "ventanas": ("Leer la ventana activa", _TOOLS_VENTANAS),
    "spotify": ("Controlar Spotify", _TOOLS_SPOTIFY),
    "manos_libres": ("Mouse gestual (manos libres)", _TOOLS_MANOS_LIBRES),
    "rutina": ("Rutina de inicio", _TOOLS_RUTINA),
}

TOOLS = tuple(t for _, tools in _REGISTRO.values() for t in tools)


def _encendida(nombre):
    return bool(config.obtener(f"personalizacion.skills.{nombre}", True))


def activas():
    """Tools de las skills encendidas en config.json."""
    return tuple(
        t for nombre, (_, tools) in _REGISTRO.items() if _encendida(nombre) for t in tools
    )


def catalogo():
    """[{nombre, etiqueta, activa}] para el panel de personalizacion."""
    return [
        {"nombre": nombre, "etiqueta": etiqueta, "activa": _encendida(nombre)}
        for nombre, (etiqueta, _) in _REGISTRO.items()
    ]
