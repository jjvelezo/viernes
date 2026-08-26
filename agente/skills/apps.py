"""Skill: abrir aplicaciones de escritorio. Adaptado de
fase6_asistente.py:24-70 (APPS_CONOCIDAS, _resolver_ejecutable, abrir_app)
-- mismo mapeo de nombres en espanol a ejecutables, con anotaciones de
tipo agregadas para que litert-lm-api arme bien el schema de la tool, y
devolviendo un texto de confirmacion en vez de None (el LLM a veces no
agrega comentario propio despues de ejecutar una tool simple, ver
core/motor_llm.py)."""

import difflib
import os

APPS_CONOCIDAS = {
    "bloc de notas": "notepad",
    "notepad": "notepad",
    "calculadora": "calc",
    "explorador": "explorer",
    "explorador de archivos": "explorer",
    "paint": "mspaint",
    "chrome": "chrome",
    "navegador": "chrome",
    "word": "winword",
    "excel": "excel",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify",
    "cmd": "cmd",
    "consola": "cmd",
    "panel de control": "control",
    "notion": "notion",
    "brave": "brave",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "discord": "discord",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "outlook": "outlook",
    "teams": "teams",
}


def _resolver_ejecutable(clave):
    if clave in APPS_CONOCIDAS:
        return APPS_CONOCIDAS[clave]
    parecidos = difflib.get_close_matches(clave, APPS_CONOCIDAS.keys(), n=1, cutoff=0.7)
    if parecidos:
        return APPS_CONOCIDAS[parecidos[0]]
    return clave


def abrir_app(nombre: str) -> str:
    """Abre una aplicacion de escritorio de Windows por su nombre.

    Args:
        nombre: nombre de la aplicacion, en espanol o el nombre del
            ejecutable -- por ejemplo "chrome", "bloc de notas", "calculadora".
    """
    ejecutable = _resolver_ejecutable(nombre.strip().lower())
    try:
        os.startfile(ejecutable)
    except OSError as error:
        raise ValueError(f'No se pudo abrir "{nombre}": {error}') from error
    return f"Abriendo {nombre}."


TOOLS = [abrir_app]
