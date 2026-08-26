"""Registro de skills: junta las tools de cada modulo en una sola lista
para pasarle al motor LLM (ver core/motor_llm.py)."""

from .apps import TOOLS as _TOOLS_APPS
from .chatgpt import TOOLS as _TOOLS_CHATGPT
from .volumen import TOOLS as _TOOLS_VOLUMEN

TOOLS = _TOOLS_APPS + _TOOLS_VOLUMEN + _TOOLS_CHATGPT
