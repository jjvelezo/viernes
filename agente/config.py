"""Configuracion local del agente.

Dos archivos, ninguno de los dos se sube a git:
  - config.json  -> ajustes NO secretos especificos de esta maquina
                    (ruta al modelo, voz de TTS, tecla de push-to-talk...).
                    Se copia de config.example.json y se completa.
  - .env         -> solo credenciales (SPOTIFY_CLIENT_ID / _SECRET).
                    Se copia de .env.example.

Se separan a proposito: config.json puede compartirse/versionarse como
ejemplo sin riesgo, .env nunca. Ver README."""

import json
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
_RUTA_CONFIG = RAIZ / "config.json"
_RUTA_EJEMPLO = RAIZ / "config.example.json"
_RUTA_ENV = RAIZ / ".env"

_config = None
_env = None


def _cargar_config():
    global _config
    if _config is None:
        if not _RUTA_CONFIG.exists():
            raise FileNotFoundError(
                f"Falta {_RUTA_CONFIG}. Copia '{_RUTA_EJEMPLO.name}' a "
                f"'config.json' y completa los valores."
            )
        _config = json.loads(_RUTA_CONFIG.read_text(encoding="utf-8"))
    return _config


def obtener(ruta, defecto=None):
    """Devuelve un valor de config.json por ruta con puntos.

    obtener("llm.model_path") -> config["llm"]["model_path"], o `defecto`
    si esa clave no esta. Una ruta tipo "~/Documents" se expande sola."""
    actual = _cargar_config()
    for parte in ruta.split("."):
        if not isinstance(actual, dict) or parte not in actual:
            return defecto
        actual = actual[parte]
    if isinstance(actual, str) and actual.startswith("~"):
        return os.path.expanduser(actual)
    return actual


def _cargar_env():
    """Parser mínimo de .env: una línea `CLAVE=valor` por credencial, sin
    comillas ni `export` (no se usa python-dotenv para no sumar dependencia
    por dos o tres claves). Las líneas en blanco y las que empiezan con `#`
    se ignoran."""
    global _env
    if _env is None:
        _env = {}
        if _RUTA_ENV.exists():
            for linea in _RUTA_ENV.read_text(encoding="utf-8").splitlines():
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    clave, valor = linea.split("=", 1)
                    _env[clave.strip()] = valor.strip()
    return _env


def secreto(clave, defecto=None):
    """Credencial de .env (o de una variable de entorno real, que tiene
    prioridad). Ej: secreto("SPOTIFY_CLIENT_ID")."""
    if clave in os.environ:
        return os.environ[clave]
    return _cargar_env().get(clave, defecto)
