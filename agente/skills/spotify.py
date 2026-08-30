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
import difflib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import win32api
import win32con
import win32gui

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
    if config.obtener("spotify.mover_a_pantalla_secundaria", True):
        rect = _pantalla_secundaria()
        if rect:
            # En un hilo aparte: mover la ventana puede tardar unos
            # segundos (el navegador todavia esta abriendo) y no queremos
            # sumar esa espera a la que ya hacemos abajo.
            threading.Thread(target=_mover_ventana_a, args=(rect, "spotify"), daemon=True).start()
    time.sleep(ESPERA_DISPOSITIVO_S)


def _pantalla_secundaria():
    """(x, y, ancho, alto) del area de trabajo de un monitor que no sea el
    primario, o None si hay una sola pantalla."""
    monitores = win32api.EnumDisplayMonitors()
    if len(monitores) < 2:
        return None
    for handle, _hdc, _rect in monitores:
        info = win32api.GetMonitorInfo(handle)
        if info.get("Flags", 0) & win32con.MONITORINFOF_PRIMARY:
            continue
        izq, arriba, der, abajo = info["Work"]
        return (izq, arriba, der - izq, abajo - arriba)
    return None


def _mover_ventana_a(rect, titulo_contiene, intentos=16, espera=0.5):
    """Espera a que aparezca una ventana visible cuyo titulo contenga
    `titulo_contiene` y la mueve/maximiza en `rect`. Para mandar el
    reproductor web de Spotify al monitor secundario cuando hay dos."""
    x, y, ancho, alto = rect
    clave = titulo_contiene.lower()
    for _ in range(intentos):
        encontradas = []

        def _cb(hwnd, _extra):
            if win32gui.IsWindowVisible(hwnd) and clave in win32gui.GetWindowText(hwnd).lower():
                encontradas.append(hwnd)
            return True

        win32gui.EnumWindows(_cb, None)
        if encontradas:
            hwnd = encontradas[0]
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetWindowPos(
                    hwnd, None, x, y, ancho, alto,
                    win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
                )
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            except Exception:
                pass  # la ventana pudo cerrarse justo ahora
            return True
        time.sleep(espera)
    return False


def _esperar_device_id(token, intentos=10, espera=1.5):
    """Sondea la lista de dispositivos hasta que aparezca uno. El
    reproductor web recien abierto suele tardar bastante mas que
    ESPERA_DISPOSITIVO_S en registrarse, y sin device_id el play se acepta
    pero queda mudo. Devuelve el id (el activo si hay, si no el primero) o
    None si nunca aparecio ninguno."""
    for _ in range(intentos):
        datos = _api("GET", "/me/player/devices", token)
        dispositivos = datos.get("devices", []) if datos else []
        if dispositivos:
            activo = next((d for d in dispositivos if d.get("is_active")), None)
            return (activo or dispositivos[0])["id"]
        time.sleep(espera)
    return None


def _device_id_o_error(token):
    """device_id listo para reproducir, o un ValueError con un mensaje que
    el agente puede decir en voz alta (en vez de afirmar en falso que puso
    la musica)."""
    device_id = _esperar_device_id(token)
    if device_id is None:
        raise ValueError(
            "Abrí Spotify en el navegador pero no logré tomar el control del "
            "reproductor. Dale play una vez y volvé a pedírmelo."
        )
    # Transferir la reproduccion a ese dispositivo de forma explicita --
    # asegura que las ordenes siguientes caigan ahi y no en un dispositivo
    # viejo que la API todavia reporte como "activo".
    _api("PUT", "/me/player", token, cuerpo={"device_ids": [device_id], "play": False})
    time.sleep(1)
    return device_id


def _reproducir_contexto(token, context_uri, total_tracks=None):
    """Reproduce una playlist/album (context_uri) en modo aleatorio,
    arrancando en una pista al azar. `total_tracks` (si se sabe) permite
    elegir el punto de arranque; sin el, solo se activa el shuffle y
    Spotify sigue en aleatorio a partir de la primera."""
    device_id = _device_id_o_error(token)
    params = {"device_id": device_id}

    _api("PUT", "/me/player/shuffle", token, parametros={**params, "state": "true"})

    cuerpo = {"context_uri": context_uri}
    if total_tracks and total_tracks > 1:
        cuerpo["offset"] = {"position": random.randint(0, min(total_tracks, 500) - 1)}
    _api("PUT", "/me/player/play", token, cuerpo=cuerpo, parametros=params)


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
    params = {"device_id": _device_id_o_error(token)}
    _api("PUT", "/me/player/shuffle", token, parametros={**params, "state": "false"})
    _api("PUT", "/me/player/play", token, cuerpo={"uris": [cancion["uri"]]}, parametros=params)
    artistas = ", ".join(a["name"] for a in cancion.get("artists", []))
    return f'Reproduciendo "{cancion["name"]}"' + (f" de {artistas}." if artistas else ".")


def _todas_mis_playlists(token):
    """Todas las playlists de la biblioteca del usuario (creadas + seguidas),
    paginando -- no solo las primeras 50. Whisper mete cualquier nombre y la
    biblioteca real puede pasar de 50, asi que quedarse en la primera pagina
    hacia que "no la encuentro" playlists que si estaban."""
    todas, offset = [], 0
    while True:
        pagina = _api("GET", "/me/playlists", token, parametros={"limit": 50, "offset": offset})
        if not pagina:
            break
        todas.extend(p for p in pagina.get("items", []) if p)
        if not pagina.get("next"):
            break
        offset += 50
    return todas


def _elegir_playlist(playlists, nombre):
    """Match tolerante a como suena una transcripcion de voz: exacto ->
    por contencion (en cualquier direccion) -> el mas parecido por
    similitud (difflib, cutoff 0.6). Mismo patron que skills/carpetas.py."""
    clave = nombre.strip().lower()
    por_nombre = {p["name"].lower(): p for p in playlists if p.get("name")}
    if clave in por_nombre:
        return por_nombre[clave]
    contenidos = [n for n in por_nombre if clave in n or n in clave]
    if contenidos:
        return por_nombre[min(contenidos, key=len)]
    parecidos = difflib.get_close_matches(clave, por_nombre.keys(), n=1, cutoff=0.6)
    return por_nombre[parecidos[0]] if parecidos else None


def reproducir_playlist(nombre: str) -> str:
    """Busca entre las playlists de la biblioteca del usuario (creadas o
    seguidas) por nombre parecido, y la reproduce en aleatorio.

    Args:
        nombre: nombre de la playlist a buscar, tal como lo dijo el usuario.
    """
    token = _obtener_token()

    coincidencia = _elegir_playlist(_todas_mis_playlists(token), nombre)
    if coincidencia is None:
        raise ValueError(f'No encontre ninguna playlist tuya parecida a "{nombre}".')

    _asegurar_dispositivo_activo(token, coincidencia["uri"])
    _reproducir_contexto(token, coincidencia["uri"], (coincidencia.get("tracks") or {}).get("total"))
    return f'Reproduciendo tu playlist "{coincidencia["name"]}" en aleatorio.'


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
    _reproducir_contexto(token, playlist["uri"], (playlist.get("tracks") or {}).get("total"))
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
