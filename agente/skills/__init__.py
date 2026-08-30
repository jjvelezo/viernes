"""Registro de skills: junta las tools de cada modulo en una sola lista
para pasarle al motor LLM (ver core/motor_llm.py)."""

from .apps import TOOLS as _TOOLS_APPS
from .carpetas import TOOLS as _TOOLS_CARPETAS
from .chatgpt import TOOLS as _TOOLS_CHATGPT
from .spotify import TOOLS as _TOOLS_SPOTIFY
from .tiempo import TOOLS as _TOOLS_TIEMPO
from .ventanas import TOOLS as _TOOLS_VENTANAS
from .volumen import TOOLS as _TOOLS_VOLUMEN

TOOLS = (
    _TOOLS_APPS
    + _TOOLS_VOLUMEN
    + _TOOLS_CHATGPT
    + _TOOLS_TIEMPO
    + _TOOLS_CARPETAS
    + _TOOLS_VENTANAS
    + _TOOLS_SPOTIFY
)
