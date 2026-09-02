"""Skill: control de Spotify (reproducir canciones/playlists/por mood,
siguiente/anterior, pausar/reanudar).

Este modulo tiene la logica de reproduccion (asegurar un dispositivo
activo, mover el reproductor web al monitor secundario, elegir playlist
por nombre) y las tools que ve el LLM. El transporte HTTP + OAuth esta en
skills/_spotify_api.py. Requiere:
  - Spotify Premium (la API de reproduccion no funciona sin eso, es un
    requisito de Spotify).
  - Haber corrido scripts/spotify_auth.py una vez (token en
    agente/.spotify_token_cache, gitignored).

La API de reproduccion necesita un "dispositivo activo" o falla con 404 --
por eso antes de reproducir se abre la pagina del track/playlist en el
navegador por defecto (asume que el usuario ya esta logueado en Spotify
ahi) y se espera un momento. La app de escritorio de Spotify no siempre
tiene un .lnk estandar en el Menu Inicio, asi que el navegador es el
camino confiable. Por que tanta gimnasia: docs/decisiones.md."""

import difflib
import random
import threading
import time
import webbrowser

import win32api
import win32con
import win32gui

import config

from ._spotify_api import _api, _buscar, _obtener_token, _uri_a_url_web, _uri_playlist

ESPERA_DISPOSITIVO_S = 6  # segundos de espera tras abrir el reproductor web antes de mandar la orden


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
    Lo que funciona: abrir siempre la pagina especifica del track/playlist
    (open.spotify.com/track/<id>) antes de llamar a la API, nunca la home.

    Fast-path: si la API YA reporta algun dispositivo (app de escritorio
    abierta, o una pestana de antes), no se abre nada ni se espera --
    _device_id_o_error() despues transfiere la reproduccion a ese
    dispositivo de forma explicita, asi que el riesgo del "dispositivo
    viejo mudo" queda cubierto igual y arrancar es mucho mas rapido."""
    datos = _api("GET", "/me/player/devices", token)
    if datos and datos.get("devices"):
        return
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


def _ventanas_visibles_con_titulo(clave):
    """[hwnd] de las ventanas visibles cuyo titulo contiene `clave` (ya en
    minusculas)."""
    encontradas = []

    def _cb(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd) and clave in win32gui.GetWindowText(hwnd).lower():
            encontradas.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    return encontradas


def _mover_ventana_a(rect, titulo_contiene, intentos=16, espera=0.5):
    """Espera a que aparezca una ventana visible cuyo titulo contenga
    `titulo_contiene` y la mueve/maximiza en `rect`. Para mandar el
    reproductor web de Spotify al monitor secundario cuando hay dos."""
    x, y, ancho, alto = rect
    clave = titulo_contiene.lower()
    for _ in range(intentos):
        encontradas = _ventanas_visibles_con_titulo(clave)
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


def reproducir_playlist_uri(uri_o_url: str) -> str:
    """Reproduce una playlist por su URI / URL / ID exacto (NO busca por
    nombre), en aleatorio. Para la rutina de inicio y para cuando el
    usuario pega un link de Spotify.

    Args:
        uri_o_url: 'spotify:playlist:...', un ID, o una URL
            https://open.spotify.com/playlist/...
    """
    token = _obtener_token()
    uri = _uri_playlist(uri_o_url)
    id_ = uri.split(":")[-1]
    info = _api("GET", f"/playlists/{id_}", token, parametros={"fields": "name,tracks.total"})
    total = (info.get("tracks") or {}).get("total") if info else None
    _asegurar_dispositivo_activo(token, uri)
    _reproducir_contexto(token, uri, total)
    nombre = info.get("name") if info else None
    return f'Reproduciendo "{nombre}" en aleatorio.' if nombre else "Reproduciendo la playlist en aleatorio."


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


_CACHE_PLAYLISTS_TTL_S = 300  # 5 min: reproducir varias playlists seguidas no re-descarga toda la biblioteca
_cache_playlists = {"data": None, "hora": 0.0}


def _todas_mis_playlists(token, usar_cache=True):
    """Todas las playlists de la biblioteca del usuario (creadas + seguidas),
    paginando -- no solo las primeras 50. Whisper mete cualquier nombre y la
    biblioteca real puede pasar de 50, asi que quedarse en la primera pagina
    hacia que "no la encuentro" playlists que si estaban.

    Se cachea el resultado unos minutos: pedir varias playlists seguidas es
    comun y re-paginar toda la biblioteca cada vez son varias llamadas HTTP.
    usar_cache=False fuerza traerlas frescas (playlist recien creada)."""
    if (
        usar_cache
        and _cache_playlists["data"] is not None
        and time.time() - _cache_playlists["hora"] < _CACHE_PLAYLISTS_TTL_S
    ):
        return _cache_playlists["data"]
    todas, offset = [], 0
    while True:
        pagina = _api("GET", "/me/playlists", token, parametros={"limit": 50, "offset": offset})
        if not pagina:
            break
        todas.extend(p for p in pagina.get("items", []) if p)
        if not pagina.get("next"):
            break
        offset += 50
    _cache_playlists["data"] = todas
    _cache_playlists["hora"] = time.time()
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
    if coincidencia is None and _cache_playlists["data"] is not None:
        # quiza es una playlist nueva creada despues del ultimo cacheo
        coincidencia = _elegir_playlist(_todas_mis_playlists(token, usar_cache=False), nombre)
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
    reproducir_playlist_uri,
    reproducir_por_mood,
    siguiente_cancion,
    cancion_anterior,
    pausar_musica,
    reanudar_musica,
]
