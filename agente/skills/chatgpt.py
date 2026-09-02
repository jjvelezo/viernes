"""Skill: preguntar a ChatGPT via navegador (Playwright).

Es una tool mas: el LLM local responde directo con su propio conocimiento
y solo llama a esta cuando el usuario nombra explicitamente ChatGPT/GPT, o
como fallback de la skill `internet`. Perfil persistente de Chromium en
privado/perfil_chatgpt/ (gitignored); no necesita login (chatgpt.com
permite mensajes anonimos de una sola vez). El contexto de Chromium se
mantiene abierto entre preguntas mientras el agente corre -- cada pregunta
solo abre una pestana nueva, no un browser nuevo -- y se cierra al salir
del proceso (atexit).

headless=True NO funciona: Cloudflare detecta el modo headless real de
Chromium y sirve "Just a moment...". La ventana fuera de pantalla tampoco:
Chromium la trata como "en segundo plano" y frena el render en tiempo real
de chatgpt.com. Lo que funciona es "--start-minimized". Detalle completo de
selectores y trampas de extraccion: docs/decisiones.md."""

import atexit
import re
import time
from pathlib import Path

PERFIL_NAVEGADOR = Path(__file__).resolve().parent.parent.parent / "privado" / "perfil_chatgpt"
PLACEHOLDER_CAJA_TEXTO = "Pregúntale a ChatGPT"
SELECTOR_TRANSCRIPCION = "[data-conversation-transcript]:visible"
TEXTO_ACEPTAR_COOKIES = "Aceptar todas"
TIEMPO_LIMITE_S = 45

# Memoria corta de continuidad: en vez de mantener una pestana/sesion de
# chatgpt.com abierta entre preguntas (fragil -- que pestana reusar, como
# saber si sigue viva) o clasificar "es la misma conversacion o no" con
# otro modelo, se guarda solo el ultimo intercambio y se le manda como
# contexto a la pregunta nueva SI paso poco tiempo. Cada pregunta sigue
# siendo un mensaje anonimo independiente en chatgpt.com (mismo mecanismo
# de siempre) -- lo que cambia es que el mensaje mismo arrastra el
# contexto necesario para que la respuesta tenga sentido como continuacion.
VENTANA_CONTINUIDAD_S = 600  # 10 minutos sin preguntar nada mas = se considera un tema nuevo
_ultimo_intercambio = None  # {"pregunta": ..., "respuesta": ..., "hora": ...}

INSTRUCCION_RESUMEN = (
    "Respondé de forma directa y concisa, sin rodeos, pero sin ser ambiguo"
    " ni omitir información importante de la respuesta — la respuesta se"
    " va a leer en voz alta. Respondé siempre con una oración completa,"
    " nunca solo un número o una palabra sueltos."
)

_playwright = None
_contexto = None


def _obtener_contexto():
    """Arranca Playwright + el contexto persistente de Chromium la primera
    vez que hace falta, y lo reutiliza en las llamadas siguientes -- evita
    repagar el arranque de Chromium en cada pregunta. Se cierra solo al
    salir del proceso (ver _cerrar/atexit mas abajo)."""
    global _playwright, _contexto
    if _contexto is None:
        # Import perezoso: Playwright (y su arranque de Chromium) solo se
        # cargan si de verdad se usa ChatGPT, no en cada arranque del agente.
        from playwright.sync_api import sync_playwright

        PERFIL_NAVEGADOR.mkdir(parents=True, exist_ok=True)
        _playwright = sync_playwright().start()
        _contexto = _playwright.chromium.launch_persistent_context(
            str(PERFIL_NAVEGADOR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--start-minimized"],
        )
        atexit.register(_cerrar)
    return _contexto


def _cerrar():
    global _playwright, _contexto
    if _contexto is not None:
        _contexto.close()
        _contexto = None
    if _playwright is not None:
        _playwright.stop()
        _playwright = None


def preguntar_a_chatgpt(pregunta: str) -> str:
    """Consulta a ChatGPT. Usar UNICAMENTE cuando el usuario nombra
    explicitamente ChatGPT o GPT ("preguntale a ChatGPT...", "busca en
    GPT...", "que dice ChatGPT de..."). Para cualquier otra busqueda o
    pregunta de info reciente, usar buscar_en_internet. Si preguntas algo
    relacionado poco despues de la pregunta anterior, se manda como
    continuacion de esa misma conversacion automaticamente -- no hace
    falta repetir el contexto.

    Args:
        pregunta: la pregunta a buscar, tal como la hizo el usuario.
    """
    global _ultimo_intercambio
    contexto = _obtener_contexto()

    contexto_previo = ""
    if _ultimo_intercambio and (time.time() - _ultimo_intercambio["hora"]) < VENTANA_CONTINUIDAD_S:
        contexto_previo = (
            f'(Para contexto: hace poco te pregunte "{_ultimo_intercambio["pregunta"]}" y '
            f'respondiste "{_ultimo_intercambio["respuesta"]}". Si la pregunta de abajo es una '
            "continuacion de eso, tenelo en cuenta; si es sobre otra cosa, respondela aparte "
            "sin problema.) "
        )
    pregunta_completa = f"{contexto_previo}{pregunta.strip()} {INSTRUCCION_RESUMEN}"

    # Pestana nueva por pregunta (no la misma de la vez anterior): cada
    # pregunta tiene que ser una conversacion nueva e independiente en
    # chatgpt.com, aunque el navegador de fondo sea el mismo.
    pagina = contexto.new_page()
    try:
        pagina.goto("https://chatgpt.com", wait_until="domcontentloaded")

        boton_cookies = pagina.get_by_text(TEXTO_ACEPTAR_COOKIES, exact=True)
        if boton_cookies.count() > 0:
            try:
                boton_cookies.first.click(timeout=3000)
            except Exception:
                pass

        caja = pagina.get_by_placeholder(PLACEHOLDER_CAJA_TEXTO)
        caja.click(timeout=TIEMPO_LIMITE_S * 1000)
        caja.fill(pregunta_completa)
        pagina.get_by_label("Enviar mensaje").click(timeout=TIEMPO_LIMITE_S * 1000)

        try:
            boton_detener = pagina.get_by_label("Detener la generación")
            boton_detener.wait_for(state="visible", timeout=5000)
            boton_detener.wait_for(state="hidden", timeout=TIEMPO_LIMITE_S * 1000)
        except Exception:
            pagina.wait_for_timeout(2000)

        transcripciones = pagina.locator(SELECTOR_TRANSCRIPCION)
        transcripciones.first.wait_for(timeout=TIEMPO_LIMITE_S * 1000)

        respuesta = ""
        for _intento in range(4):
            for indice in range(transcripciones.count()):
                texto_completo = transcripciones.nth(indice).inner_text()
                respuesta = _extraer_respuesta(texto_completo, pregunta_completa)
                if respuesta:
                    break
            if respuesta:
                break
            pagina.wait_for_timeout(500)

        if not respuesta:
            raise ValueError("ChatGPT no respondió a tiempo.")
        _ultimo_intercambio = {"pregunta": pregunta, "respuesta": respuesta, "hora": time.time()}
        return respuesta
    finally:
        pagina.close()


PATRON_RUIDO_CITAS = re.compile(
    r"^(fuentes?|\+\d+|[\w.-]+\.\w{2,})$", re.IGNORECASE
)  # "Fuentes", "+2", o un dominio suelto tipo "primero.com" -- los chips
# de citas que chatgpt.com agrega al final de una respuesta con fuentes


def _extraer_respuesta(transcripcion_completa, pregunta):
    partes = [parte.strip() for parte in transcripcion_completa.split("\n\n") if parte.strip()]
    if pregunta not in partes:
        partes_respuesta = partes
    else:
        indice = partes.index(pregunta)
        partes_respuesta = partes[indice + 2 :]

    # Los chips de citas vienen como fragmentos sueltos y cortos al final
    # (una letra, un dominio, "Fuentes") -- se descartan.
    while partes_respuesta and (
        len(partes_respuesta[-1]) <= 3 or PATRON_RUIDO_CITAS.match(partes_respuesta[-1])
    ):
        partes_respuesta.pop()

    return "\n\n".join(partes_respuesta).strip()


TOOLS = [preguntar_a_chatgpt]
