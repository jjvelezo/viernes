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
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
_RUTA_CONFIG = RAIZ / "config.json"
_RUTA_EJEMPLO = RAIZ / "config.example.json"
_RUTA_ENV = RAIZ / ".env"

_config = None
_env = None

# Serializa las escrituras a config.json (el panel de personalizacion de la
# ventana de chat escribe desde el hilo de pywebview mientras otros hilos leen).
_lock = threading.Lock()


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


def escribir(ruta, valor):
    """Guarda un valor en config.json por ruta con puntos, creando los dicts
    intermedios que falten. Reescribe el archivo entero (indentado, con acentos)
    y actualiza el cache en memoria. Lo usa el panel de personalizacion del chat.

    escribir("personalizacion.paleta", "verde")
    escribir("push_to_talk.key", "f8")
    """
    escribir_varios({ruta: valor})


def escribir_varios(pares):
    """Como escribir() pero para varias claves de una, reescribiendo el
    archivo una sola vez. `pares` es {ruta_con_puntos: valor}."""
    with _lock:
        cfg = _cargar_config()
        for ruta, valor in pares.items():
            partes = ruta.split(".")
            actual = cfg
            for parte in partes[:-1]:
                siguiente = actual.get(parte)
                if not isinstance(siguiente, dict):
                    siguiente = {}
                    actual[parte] = siguiente
                actual = siguiente
            actual[partes[-1]] = valor
        _RUTA_CONFIG.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def ejemplo(ruta, defecto=None):
    """Valor de config.example.json (la plantilla) por ruta con puntos. Se usa
    para el boton 'volver a los valores por defecto' del panel de chat."""
    try:
        datos = json.loads(_RUTA_EJEMPLO.read_text(encoding="utf-8"))
    except Exception:
        return defecto
    for parte in ruta.split("."):
        if not isinstance(datos, dict) or parte not in datos:
            return defecto
        datos = datos[parte]
    return datos


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
