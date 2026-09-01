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
  - rutina.spotify_horario: {"inicio": 7, "fin": 20} -> la playlist solo
    se pone en el arranque automatico (doble palmada / correr main.py) si
    la hora actual esta en esa franja. Fuera de ese rango, la rutina abre
    las apps igual pero NO toca Spotify. El pedido explicito por voz
    ("arranca mi jornada") ignora la franja y siempre pone la playlist.
"""

import logging
import os
import time
from pathlib import Path

import config
from . import apps as _apps
from . import spotify as _spotify

_LOG = logging.getLogger("agente.rutina")


def _abrir_apps():
    for entrada in config.obtener("rutina.apps", []) or []:
        entrada = str(entrada)
        try:
            if (os.sep in entrada or "/" in entrada) and Path(entrada).exists():
                os.startfile(entrada)
            else:
                _apps.abrir_app(entrada)
        except Exception as error:
            # una app que no esta no debe frenar el resto de la rutina
            _LOG.warning("Rutina: no se pudo abrir %r (%s)", entrada, error)


def _en_horario_spotify() -> bool:
    """True si la hora actual cae en rutina.spotify_horario (por defecto
    7-20). El rango puede cruzar medianoche (inicio > fin)."""
    franja = config.obtener("rutina.spotify_horario") or {}
    inicio = int(franja.get("inicio", 7))
    fin = int(franja.get("fin", 20))
    hora = time.localtime().tm_hour
    if inicio <= fin:
        return inicio <= hora < fin
    return hora >= inicio or hora < fin  # cruza medianoche


def _poner_playlist(respetar_horario: bool = False):
    uri = config.obtener("rutina.spotify_playlist_uri")
    if not uri:
        return
    if respetar_horario and not _en_horario_spotify():
        return  # fuera de la franja horaria: la rutina sigue sin Spotify
    try:
        _spotify.reproducir_playlist_uri(uri)
    except Exception as error:
        # sin token / Premium / dispositivo: la rutina sigue igual
        _LOG.warning("Rutina: no se pudo poner la playlist (%s)", error)


def iniciar_jornada() -> str:
    """Abre el entorno de trabajo del usuario: las apps configuradas y su
    playlist de siempre en aleatorio. Usar cuando diga "arranca mi
    jornada", "modo trabajo", "abrime todo para trabajar" o similar."""
    _abrir_apps()
    _poner_playlist()
    return "Listo, te abrí el entorno de trabajo."


def ejecutar_al_inicio():
    """Lo mismo que iniciar_jornada() pero sin texto de vuelta y
    respetando rutina.spotify_horario -- lo llama main.py al arrancar el
    agente (tanto al correrlo a mano como al lanzarlo por doble palmada).
    Fuera de la franja abre las apps pero no pone Spotify."""
    _abrir_apps()
    _poner_playlist(respetar_horario=True)


TOOLS = [iniciar_jornada]
