#!/usr/bin/env python3
"""Autorizacion OAuth de Spotify -- correr UNA sola vez (o cuando el
refresh_token deje de servir). Reusa una app de Spotify Developer ya
registrada (el modo Development de Spotify permite reusar una app entre
proyectos propios sin crear una nueva); el CLIENT_ID/SECRET salen de
agente/.env.

Abre el navegador para que el usuario autorice, y guarda el token en
agente/.spotify_token_cache (gitignored) para que skills/spotify.py lo
use despues sin pedir login de nuevo."""

import base64
import http.server
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))  # para importar config.py de agente/
import config  # noqa: E402

CACHE_PATH = RAIZ / ".spotify_token_cache"
REDIRECT_URI = "http://127.0.0.1:8888/callback"

# Permisos pedidos: leer/controlar reproduccion, leer las playlists del
# usuario (para reproducir_playlist por nombre).
SCOPES = "user-modify-playback-state user-read-playback-state playlist-read-private"

_codigo_auth = None


class _ManejadorCallback(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _codigo_auth
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _codigo_auth = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<h2>Autorizado. Ya podes cerrar esta ventana.</h2>".encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no se recibio el codigo de autorizacion.")

    def log_message(self, *args):
        pass  # silenciar el log del servidor


def _url_autorizacion(client_id):
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
    return f"https://accounts.spotify.com/authorize?{params}"


def _intercambiar_codigo(codigo, client_id, client_secret):
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": REDIRECT_URI,
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
        datos_token = json.loads(respuesta.read())

    datos_token["expires_at"] = int(time.time()) + datos_token["expires_in"]
    with open(CACHE_PATH, "w", encoding="utf-8") as archivo:
        json.dump(datos_token, archivo, indent=2)

    print(f"[auth] Token guardado en {CACHE_PATH}")
    return datos_token


def main():
    client_id = config.secreto("SPOTIFY_CLIENT_ID")
    client_secret = config.secreto("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Falta SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET en agente/.env (copia .env.example).")

    servidor = http.server.HTTPServer(("127.0.0.1", 8888), _ManejadorCallback)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    url = _url_autorizacion(client_id)
    print("[auth] Abriendo Spotify para que autorices...")
    print(f"[auth] Si no abre solo, copia esta URL en el navegador: {url}")
    webbrowser.open(url)

    print("[auth] Esperando que autorices en el navegador...")
    while _codigo_auth is None:
        time.sleep(0.5)

    servidor.shutdown()
    _intercambiar_codigo(_codigo_auth, client_id, client_secret)
    print("[auth] Listo. El agente ya puede controlar Spotify.")


if __name__ == "__main__":
    main()
