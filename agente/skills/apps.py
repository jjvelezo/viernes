"""Skill: abrir aplicaciones de escritorio. Reescrita de un prototipo
previo, con un cambio de fondo: en vez de un diccionario fijo a mano (que
se desactualiza solo con instalar/desinstalar algo, y puede ofrecerle al
LLM apps que el usuario ni tiene -- ej. "Notion"), se
arma un indice real de lo que esta instalado leyendo los accesos directos
del Menu Inicio de Windows (las dos carpetas estandar de "Start Menu\\
Programs", la de todos los usuarios y la del usuario actual). Cada acceso
directo (.lnk) se resuelve a su ejecutable real via WScript.Shell (mismo
mecanismo que usa el propio Explorador de Windows), y ese es el nombre +
ruta que se usa para abrir la app y para el fuzzy-match de nombres
transcriptos por voz."""

import difflib
import os

import win32com.client

APPS_CONOCIDAS = {
    # Solo utilidades del sistema, casi nunca tienen un .lnk lindo en el
    # Menu Inicio (viven en PATH directo) -- el resto se resuelve con el
    # indice dinamico de mas abajo.
    "bloc de notas": "notepad",
    "notepad": "notepad",
    "calculadora": "calc",
    "explorador": "explorer",
    "explorador de archivos": "explorer",
    "paint": "mspaint",
    "cmd": "cmd",
    "consola": "cmd",
    "panel de control": "control",
}

CARPETAS_MENU_INICIO = (
    os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
)

_indice_instaladas = None  # cache: {nombre_normalizado: ruta_ejecutable}, se arma la primera vez que hace falta


def _construir_indice_instaladas():
    """Recorre el Menu Inicio y arma {nombre: ruta.exe} con lo que esta
    REALMENTE instalado. No falla si algo no se puede leer -- un acceso
    directo roto o sin permiso simplemente se salta."""
    indice = {}
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        return indice  # COM no disponible por algun motivo raro: seguimos sin indice dinamico

    for carpeta in CARPETAS_MENU_INICIO:
        if not carpeta or not os.path.isdir(carpeta):
            continue
        for raiz, _dirs, archivos in os.walk(carpeta):
            for archivo in archivos:
                if not archivo.lower().endswith(".lnk"):
                    continue
                try:
                    destino = shell.CreateShortcut(os.path.join(raiz, archivo)).Targetpath
                except Exception:
                    continue
                if destino and destino.lower().endswith(".exe"):
                    nombre = os.path.splitext(archivo)[0].strip().lower()
                    indice[nombre] = destino
    return indice


def _obtener_indice_instaladas():
    global _indice_instaladas
    if _indice_instaladas is None:
        _indice_instaladas = _construir_indice_instaladas()
    return _indice_instaladas


def _resolver_ejecutable(clave):
    """Primero las utilidades del sistema conocidas, despues coincidencia
    exacta y despues difusa contra lo que esta REALMENTE instalado (nunca
    contra nombres inventados) -- si nada matchea, se intenta `clave` tal
    cual como ultimo recurso (por si es un ejecutable en PATH que no tiene
    acceso directo, ej. herramientas de linea de comandos)."""
    if clave in APPS_CONOCIDAS:
        return APPS_CONOCIDAS[clave]

    indice = _obtener_indice_instaladas()
    if clave in indice:
        return indice[clave]

    parecidos = difflib.get_close_matches(clave, indice.keys(), n=1, cutoff=0.7)
    if parecidos:
        return indice[parecidos[0]]

    return clave


def abrir_app(nombre: str) -> str:
    """Abre una aplicacion de escritorio de Windows por su nombre. Solo
    puede abrir aplicaciones que esten REALMENTE instaladas en esta PC (se
    valida contra el Menu Inicio) -- si el usuario pide una app que no
    tiene instalada, esta funcion va a fallar y hay que decirselo, no
    inventar que se abrio.

    Args:
        nombre: nombre de la aplicacion, en espanol o el nombre del
            ejecutable -- por ejemplo "chrome", "bloc de notas", "calculadora".
    """
    ejecutable = _resolver_ejecutable(nombre.strip().lower())
    try:
        os.startfile(ejecutable)
    except OSError as error:
        raise ValueError(f'No se pudo abrir "{nombre}": no parece estar instalada ({error}).') from error
    return f"Abriendo {nombre}."


TOOLS = [abrir_app]
