"""Skill: rutina de inicio del usuario ("flow Jarvis").

Se dispara sola al arrancar el agente -- main.py la corre en un hilo
aparte, EN PARALELO con la carga de Whisper y Gemma, para que abrir
Viernes no se sienta lento (Cursor y Spotify aparecen mientras cargan los
modelos). Tambien se puede pedir por voz ("arranca mi jornada", "modo
trabajo").

Que hace, desde config.json:
  - rutina.apps: lista de apps (nombre o ruta a .exe) -> las abre.
  - rutina.spotify_playlist_uri: URI/URL/ID de playlist -> la pone en
    aleatorio (y a la 2da pantalla si hay dos, ver skills/spotify.py).
"""

import os
from pathlib import Path

import config
from . import apps as _apps
from . import spotify as _spotify


def _abrir_apps():
    for entrada in config.obtener("rutina.apps", []) or []:
        entrada = str(entrada)
        try:
            if (os.sep in entrada or "/" in entrada) and Path(entrada).exists():
                os.startfile(entrada)
            else:
                _apps.abrir_app(entrada)
        except Exception:
            pass  # una app que no esta no debe frenar el resto de la rutina


def _poner_playlist():
    uri = config.obtener("rutina.spotify_playlist_uri")
    if not uri:
        return
    try:
        _spotify.reproducir_playlist_uri(uri)
    except Exception:
        pass  # sin token / Premium / dispositivo: la rutina sigue igual


def iniciar_jornada() -> str:
    """Abre el entorno de trabajo del usuario: las apps configuradas y su
    playlist de siempre en aleatorio. Usar cuando diga "arranca mi
    jornada", "modo trabajo", "abrime todo para trabajar" o similar."""
    _abrir_apps()
    _poner_playlist()
    return "Listo, te abrí el entorno de trabajo."


def ejecutar_al_inicio():
    """Lo mismo que iniciar_jornada() pero sin texto de vuelta -- lo llama
    main.py al arrancar el agente."""
    _abrir_apps()
    _poner_playlist()


TOOLS = [iniciar_jornada]
