"""Logging del agente: cada turno (comando transcripto, tools llamadas con
sus argumentos y resultado, respuesta final, errores, latencia) queda
guardado en un archivo para poder revisar/depurar despues. privado/ ya es
la carpeta de cosas locales que no se suben a git (los logs van a tener
transcripciones de voz, no deberian subirse)."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

CARPETA_LOGS = Path(__file__).resolve().parent.parent.parent / "privado" / "logs"
ARCHIVO_LOG = CARPETA_LOGS / "agente.log"


def configurar():
    """Arma el logger "agente" (archivo rotativo + consola) y lo devuelve.
    Idempotente: si ya se configuro antes, no duplica handlers."""
    logger = logging.getLogger("agente")
    if logger.handlers:
        return logger

    CARPETA_LOGS.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)

    manejador_archivo = RotatingFileHandler(
        ARCHIVO_LOG, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    manejador_archivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(manejador_archivo)

    # Consola solo si hay una de verdad (se corrio a mano para depurar). En el
    # arranque normal (bandeja / doble palmada) corre bajo pythonw.exe, sin
    # consola: escribir ahi es trabajo perdido.
    flujo = sys.stderr
    if flujo is not None and hasattr(flujo, "isatty") and flujo.isatty():
        manejador_consola = logging.StreamHandler()
        manejador_consola.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(manejador_consola)

    return logger
