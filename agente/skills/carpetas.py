"""Skill: abrir carpetas conocidas (Escritorio, Documentos, Descargas) y
proyectos dentro de Documents/Proyectos -- mismo mapeo de carpetas base
que ya usa fase6_asistente.leer_ventana_activa() (CARPETAS_BUSQUEDA)."""

import difflib
import os
from pathlib import Path

CARPETA_PROYECTOS = Path.home() / "Documents" / "Proyectos"

CARPETAS_CONOCIDAS = {
    "escritorio": Path.home() / "Desktop",
    "documentos": Path.home() / "Documents",
    "descargas": Path.home() / "Downloads",
    "proyectos": CARPETA_PROYECTOS,
}


def _buscar_proyecto(clave):
    """Busca `clave` entre las subcarpetas de Documents/Proyectos --
    exacto primero, despues por contencion (ej. "jarvis" dentro de
    "jarvis_template" -- nombres con sufijos como "_template"/"-aws"/" v2"
    son comunes y el fuzzy-match por similitud total los deja afuera),
    y por ultimo el mas parecido en general (los nombres vienen de una
    transcripcion de voz, no van a ser exactos siempre)."""
    if not CARPETA_PROYECTOS.is_dir():
        return None
    subcarpetas = {p.name.lower(): p for p in CARPETA_PROYECTOS.iterdir() if p.is_dir()}
    if clave in subcarpetas:
        return subcarpetas[clave]

    contenidos = [nombre for nombre in subcarpetas if clave in nombre or nombre in clave]
    if contenidos:
        # El mas corto es el match mas "ajustado" (menos sufijo de sobra).
        return subcarpetas[min(contenidos, key=len)]

    parecidos = difflib.get_close_matches(clave, subcarpetas.keys(), n=1, cutoff=0.6)
    if parecidos:
        return subcarpetas[parecidos[0]]
    return None


def abrir_carpeta(nombre: str) -> str:
    """Abre en el Explorador de Windows una carpeta conocida (escritorio,
    documentos, descargas) o un proyecto dentro de la carpeta Proyectos
    del usuario -- no hace falta decir el nombre exacto del proyecto.

    Args:
        nombre: nombre de la carpeta o proyecto a abrir, por ejemplo
            "descargas", "viernes", o "ia linkedin".
    """
    clave = nombre.strip().lower()
    ruta = CARPETAS_CONOCIDAS.get(clave) or _buscar_proyecto(clave)
    if ruta is None or not ruta.is_dir():
        raise ValueError(f'No encontre la carpeta "{nombre}".')
    os.startfile(str(ruta))
    return f"Abriendo {nombre}."


TOOLS = [abrir_carpeta]
