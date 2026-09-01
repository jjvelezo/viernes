"""Transporte de la Web API de Spotify: cache de token, refresh OAuth y
llamadas HTTP crudas. Sin dependencias de Windows -- todo `urllib` + `json`,
para poder testearlo aislado. La skill (skills/spotify.py) usa estas
funciones; el flujo de autorizacion inicial esta en scripts/spotify_auth.py.

Detalles de por que la reproduccion necesita tanta gimnasia (dispositivo
activo, transferencia explicita, limit>=3 en las busquedas): CLAUDE.md.
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

RAIZ = Path(__file__).resolve().parent.parent
CACHE_PATH = RAIZ / ".spotify_token_cache"


def _leer_cache():
    if not CACHE_PATH.exists():
        return {}
    with open(CACHE_PATH, encoding="utf-8") as archivo:
        return json.load(archivo)


def _guardar_cache(datos):
    with open(CACHE_PATH, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, indent=2)


def _refrescar_token(cache, client_id, client_secret):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": cache["refresh_token"],
    }).encode()
    credenciales = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    peticion = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={
            "Authorization": f"Basic {credenciales}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(peticion) as respuesta:
        nuevos_datos = json.loads(respuesta.read())

    cache["access_token"] = nuevos_datos["access_token"]
    cache["expires_at"] = int(time.time()) + nuevos_datos["expires_in"]
    if "refresh_token" in nuevos_datos:
        cache["refresh_token"] = nuevos_datos["refresh_token"]
    _guardar_cache(cache)
    return cache


def _obtener_token():
    client_id = config.secreto("SPOTIFY_CLIENT_ID")
    client_secret = config.secreto("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("Falta configurar SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET en agente/.env")

    cache = _leer_cache()
    if not cache:
        raise ValueError(
            "Spotify no esta autorizado todavia -- corre agente/scripts/spotify_auth.py una vez."
        )

    if time.time() >= cache.get("expires_at", 0) - 60:
        cache = _refrescar_token(cache, client_id, client_secret)

    return cache["access_token"]


def _api(metodo, endpoint, token, cuerpo=None, parametros=None):
    """Llamada generica a la Web API de Spotify. Devuelve el JSON de
    respuesta (o None si no hay cuerpo, ej. 204 No Content)."""
    url = f"https://api.spotify.com/v1{endpoint}"
    if parametros:
        url += f"?{urllib.parse.urlencode(parametros)}"
    data = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(
        url,
        data=data,
        method=metodo,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(peticion) as respuesta:
            crudo = respuesta.read()
            if not crudo or not crudo.strip():
                return None  # 204 No Content (lo normal en play/pause/shuffle/next)
            try:
                return json.loads(crudo)
            except json.JSONDecodeError:
                # Algunas respuestas OK de Spotify traen un cuerpo que no es
                # JSON (o un 200 con cuerpo vacio raro) -- no es un error,
                # simplemente no hay nada que devolver.
                return None
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise ValueError("Spotify rechazo la accion -- revisa que la cuenta tenga Premium.") from error
        if error.code == 404:
            return None  # sin dispositivo activo, o sin reproduccion en curso -- se maneja en cada funcion
        raise


def _buscar(token, consulta, tipo, limite=5):
    resultado = _api("GET", "/search", token, parametros={"q": consulta, "type": tipo, "limit": limite})
    if not resultado:
        return []
    # El campo viene en plural (tracks/playlists) -- y desde la migracion
    # de la API de feb-2026 pueden aparecer items nulos en resultados de
    # playlist, se filtran.
    items = resultado.get(f"{tipo}s", {}).get("items", [])
    return [item for item in items if item]


def _uri_a_url_web(uri_spotify):
    """spotify:track:XXXX -> https://open.spotify.com/track/XXXX (y lo
    mismo para playlist/album/etc)."""
    partes = uri_spotify.split(":")
    tipo, id_ = partes[1], partes[2]
    return f"https://open.spotify.com/{tipo}/{id_}"


def _uri_playlist(texto):
    """Acepta 'spotify:playlist:ID', un ID pelado o una URL
    https://open.spotify.com/playlist/ID?... y devuelve 'spotify:playlist:ID'."""
    texto = texto.strip()
    if texto.startswith("spotify:playlist:"):
        return texto
    if "open.spotify.com/playlist/" in texto:
        cola = texto.split("/playlist/", 1)[1]
        id_ = cola.split("?", 1)[0].split("/", 1)[0]
        return f"spotify:playlist:{id_}"
    return f"spotify:playlist:{texto}"
