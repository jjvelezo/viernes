"""Skill: buscar en internet (Tavily). Camino por defecto para preguntas
que piden info que el modelo local no tiene (reciente, actualizada) o
cuando el usuario dice "busca ...".

Tavily y no scrapear un buscador: su endpoint /search con include_answer
devuelve una respuesta YA redactada a partir de varias fuentes -- Gemma
4 E2B (2B, resumidor flojo) no tiene que sintetizar nada, se dice tal
cual. Si Tavily falla o no da respuesta, cae a preguntar_a_chatgpt()
(Playwright) -- el mismo rol de fallback que tenia antes de sumar esta
skill.

Contexto del usuario: Tavily no tiene un campo "system prompt" para la
redaccion de la respuesta, asi que el contexto (pais, moneda, zona
horaria) se ANTEPONE al texto de la consulta -- config internet.contexto_usuario.
Sin esto, "cuanto vale el dolar" salia en pesos mexicanos. Ademas se
manda `country` (config internet.pais) para sesgar los resultados.

Key gratuita (free tier ~1000 busquedas/mes) en https://tavily.com ->
TAVILY_API_KEY en agente/.env. Sin key, esta skill usa el fallback
directamente.

Llamada directa con urllib (sin SDK), mismo patron que skills/spotify.py."""

import json
import logging
import urllib.error
import urllib.request

import config

from .chatgpt import preguntar_a_chatgpt as _fallback_chatgpt

_LOG = logging.getLogger("agente.internet")
_ENDPOINT = "https://api.tavily.com/search"
_TIEMPO_LIMITE_S = 20


def _tavily(pregunta, api_key):
    cuerpo = {
        "query": pregunta,
        "include_answer": "basic",   # respuesta corta ya redactada
        "search_depth": "basic",     # 1 credito; "advanced" = 2, mas completa
        "max_results": 5,
    }
    pais = config.obtener("internet.pais")
    if pais:
        cuerpo["country"] = pais     # sesga resultados hacia ese pais
    peticion = urllib.request.Request(
        _ENDPOINT,
        data=json.dumps(cuerpo).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(peticion, timeout=_TIEMPO_LIMITE_S) as respuesta:
        return json.loads(respuesta.read())


def buscar_en_internet(pregunta: str) -> str:
    """Busca en internet la respuesta a algo que no sabes con certeza o que
    puede haber cambiado (noticias, clima, precios, resultados deportivos,
    datos recientes), o cuando el usuario pide "busca ..." / "fijate ..."
    SIN nombrar ninguna herramienta. Devuelve una respuesta corta ya
    redactada, lista para leer en voz alta.

    Args:
        pregunta: la pregunta a buscar, tal como la hizo el usuario.
    """
    api_key = config.secreto("TAVILY_API_KEY")
    if not api_key:
        _LOG.info("Sin TAVILY_API_KEY -> fallback a ChatGPT")
        return _fallback_chatgpt(pregunta)

    contexto = config.obtener("internet.contexto_usuario", "")
    consulta = f"{contexto} {pregunta}".strip() if contexto else pregunta

    try:
        datos = _tavily(consulta, api_key)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        _LOG.warning("Tavily fallo (%s) -> fallback a ChatGPT", error)
        return _fallback_chatgpt(pregunta)

    respuesta = (datos.get("answer") or "").strip()
    if not respuesta:
        _LOG.info("Tavily no redacto answer -> fallback a ChatGPT")
        return _fallback_chatgpt(pregunta)

    _LOG.info("Respondio Tavily")
    return respuesta


TOOLS = [buscar_en_internet]
