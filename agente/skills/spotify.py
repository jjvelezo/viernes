"""Skill: control de Spotify (reproducir canciones/playlists/por mood,
siguiente/anterior, pausar/reanudar). Adaptado del mismo patron que uso
jarvis_template (Documents/Proyectos/jarvis_template/scripts/launch.py) --
llamadas directas a la Web API de Spotify con `urllib`, sin libreria
externa, reusando la misma app "Jarvis" ya registrada en el Developer
Dashboard del usuario. Requiere:
  - Spotify Premium en la cuenta (la API de reproduccion no funciona sin
    eso, es un requisito de Spotify, no de este codigo).
  - Haber corrido scripts/spotify_auth.py una vez (guarda el token en
    .spotify_token_cache, gitignored).

La API de reproduccion necesita un "dispositivo activo" (Spotify corriendo
en algun lado) o falla con 404 -- por eso antes de reproducir se asegura
de que haya uno, abriendo el reproductor web (open.spotify.com) en el
navegador por defecto y esperando un momento antes de mandar la orden.
Igual que jarvis_template: la app de escritorio de Spotify no siempre
tiene un acceso directo estandar en el Menu Inicio (se probo abrir_app y
fallo -- "no encontrado"), asi que el navegador es el camino confiable.
Asume que el usuario ya esta logueado en Spotify en su navegador por
defecto (si no lo esta, va a mostrar la pantalla de login en vez de
activar un dispositivo, y la reproduccion va a seguir fallando)."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import config

RAIZ = Path(__file__).resolve().parent.parent
CACHE_PATH = RAIZ / ".spotify_token_cache"

ESPERA_DISPOSITIVO_S = 6  # mismo valor que ya funcionaba en jarvis_template (spotify_api_initial_delay_seconds)


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
            return json.loads(crudo) if crudo else None
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise ValueError("Spotify rechazo la accion -- revisa que la cuenta tenga Premium.") from error
        if error.code == 404:
            return None  # sin dispositivo activo, o sin reproduccion en curso -- se maneja en cada funcion
        raise


def _uri_a_url_web(uri_spotify):
    """spotify:track:XXXX -> https://open.spotify.com/track/XXXX (y lo
    mismo para playlist/album/etc)."""
    partes = uri_spotify.split(":")
    tipo, id_ = partes[1], partes[2]
    return f"https://open.spotify.com/{tipo}/{id_}"


def _asegurar_dispositivo_activo(token, uri_contenido):
    """Abre la pagina especifica de lo que se va a reproducir (no la home
    de open.spotify.com) y espera un momento, SIEMPRE -- sin condicionarlo
    a si la API dice que ya hay un dispositivo activo. Dos cosas se
    probaron y no andaban:
      1) chequear primero si la API reporta un dispositivo activo antes de
         abrir nada -- fallaba porque un dispositivo de una pestana vieja
         puede seguir figurando "activo" aunque ya no este realmente
         escuchando, y las ordenes de reproduccion quedaban aceptadas sin
         error pero mudas.
      2) abrir la home generica (open.spotify.com) en vez de la pagina
         especifica del track/playlist -- no llegaba a registrar ningun
         dispositivo ni despues de 16s de espera.
    jarvis_template siempre abria la pagina especifica del track
    (open.spotify.com/track/<id>) antes de llamar a la API, nunca la
    home -- por eso se replica exacto eso aca."""
    webbrowser.open(_uri_a_url_web(uri_contenido))
    time.sleep(ESPERA_DISPOSITIVO_S)


def _buscar(token, consulta, tipo, limite=5):
    resultado = _api("GET", "/search", token, parametros={"q": consulta, "type": tipo, "limit": limite})
    if not resultado:
        return []
    # El campo viene en plural (tracks/playlists) -- y desde la migracion
    # de la API de feb-2026 pueden aparecer items nulos en resultados de
    # playlist, se filtran.
    items = resultado.get(f"{tipo}s", {}).get("items", [])
    return [item for item in items if item]


def reproducir_cancion(nombre: str) -> str:
    """Busca una cancion por nombre y la reproduce en Spotify.

    Args:
        nombre: nombre de la cancion (y opcionalmente artista), tal como
            lo dijo el usuario -- por ejemplo "bohemian rhapsody" o
            "shape of you de ed sheeran".
    """
    token = _obtener_token()
    # OJO: pedirle a la API limit=1 devuelve resultados incorrectos (visto
    # en pruebas reales -- "bohemian rhapsody" con limit=1 daba "Billie
    # Jean" como primer resultado; con limit=3+ el primero ya es el
    # correcto). Se pide de a varios y se toma el primero.
    resultados = _buscar(token, nombre, "track", limite=3)
    if not resultados:
        raise ValueError(f'No encontre ninguna cancion parecida a "{nombre}" en Spotify.')
    cancion = resultados[0]
    _asegurar_dispositivo_activo(token, cancion["uri"])
    _api("PUT", "/me/player/play", token, cuerpo={"uris": [cancion["uri"]]})
    artistas = ", ".join(a["name"] for a in cancion.get("artists", []))
    return f'Reproduciendo "{cancion["name"]}"' + (f" de {artistas}." if artistas else ".")


def reproducir_playlist(nombre: str) -> str:
    """Busca entre las playlists GUARDADAS del usuario (las suyas, no
    cualquiera de Spotify) por nombre parecido, y la reproduce.

    Args:
        nombre: nombre de la playlist a buscar, tal como lo dijo el usuario.
    """
    token = _obtener_token()
    clave = nombre.strip().lower()

    # Solo la primera pagina (hasta 50 playlists) -- alcanza de sobra para
    # una biblioteca personal, sin la complejidad de paginar de a mas.
    pagina = _api("GET", "/me/playlists", token, parametros={"limit": 50})
    propias = pagina.get("items", []) if pagina else []

    coincidencia = next((p for p in propias if p and clave in p["name"].lower()), None)
    if coincidencia is None:
        raise ValueError(f'No encontre ninguna playlist tuya parecida a "{nombre}".')

    _asegurar_dispositivo_activo(token, coincidencia["uri"])
    _api("PUT", "/me/player/play", token, cuerpo={"context_uri": coincidencia["uri"]})
    return f'Reproduciendo tu playlist "{coincidencia["name"]}".'


def reproducir_por_mood(mood: str) -> str:
    """Busca una playlist publica/curada de Spotify que coincida con un
    mood, genero o actividad (ej. "algo relajado", "para entrenar",
    "musica triste") y la reproduce -- usar cuando el usuario pide un
    tipo de musica en vez de una cancion o playlist especifica.

    Args:
        mood: el mood/genero/actividad que describio el usuario.
    """
    token = _obtener_token()
    resultados = _buscar(token, mood, "playlist", limite=5)
    if not resultados:
        raise ValueError(f'No encontre ninguna playlist para "{mood}".')
    playlist = resultados[0]
    _asegurar_dispositivo_activo(token, playlist["uri"])
    _api("PUT", "/me/player/play", token, cuerpo={"context_uri": playlist["uri"]})
    return f'Reproduciendo "{playlist["name"]}".'


def siguiente_cancion() -> str:
    """Pasa a la siguiente cancion en Spotify."""
    token = _obtener_token()
    _api("POST", "/me/player/next", token)
    return "Siguiente cancion."


def cancion_anterior() -> str:
    """Vuelve a la cancion anterior en Spotify."""
    token = _obtener_token()
    _api("POST", "/me/player/previous", token)
    return "Cancion anterior."


def pausar_musica() -> str:
    """Pausa la reproduccion actual de Spotify."""
    token = _obtener_token()
    _api("PUT", "/me/player/pause", token)
    return "Musica pausada."


def reanudar_musica() -> str:
    """Reanuda la reproduccion de Spotify donde haya quedado pausada. No
    abre ninguna pagina nueva -- a diferencia de reproducir_cancion/
    playlist/mood, esto asume que ya hay un dispositivo (el que se pauso);
    si no hay ninguno, la API va a fallar y hay que decirle al usuario que
    no hay nada para reanudar, no inventar que se reanudo algo."""
    token = _obtener_token()
    _api("PUT", "/me/player/play", token)
    return "Reanudando la musica."


TOOLS = [
    reproducir_cancion,
    reproducir_playlist,
    reproducir_por_mood,
    siguiente_cancion,
    cancion_anterior,
    pausar_musica,
    reanudar_musica,
]
