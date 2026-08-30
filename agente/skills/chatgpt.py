"""Skill: preguntar a ChatGPT via navegador (Playwright). Adaptado de
fase6_asistente.py:73-186 -- misma automatizacion (perfil persistente de
Chromium, boton "Detener la generacion" para saber cuando termino de
responder, extraccion del transcript). A diferencia de fase9_asistente_voz
(donde esto era el camino por defecto para "dudas"), aca es una tool mas:
el LLM local responde directo con su propio conocimiento, y solo llama a
esta cuando el usuario pide explicitamente buscar en internet / preguntarle
a ChatGPT, o el pedido necesita informacion que el modelo local no puede
saber (reciente, actualizada).

A diferencia de fase6_asistente.py (que abre y cierra un navegador nuevo
en cada pregunta), aca el contexto de Chromium se mantiene abierto entre
preguntas mientras el agente esta corriendo -- cada pregunta solo abre una
pestana nueva, no un browser nuevo, asi que las preguntas despues de la
primera son mas rapidas (no repaga el arranque de Chromium). Se cierra
solo al salir del proceso (atexit) -- nada de esto persiste en disco mas
alla del perfil de Chromium ya gitignoreado en privado/perfil_chatgpt/.

headless=True NO funciona aca -- probado y confirmado: chatgpt.com esta
detras de Cloudflare, que detecta el modo headless real de Chromium y
sirve una pantalla de verificacion ("Just a moment...") en vez del sitio.
Posicionar la ventana fuera de pantalla (headed pero invisible) TAMPOCO
funciona -- Chromium parece tratar una ventana fuera de pantalla como "en
segundo plano" y frena el renderizado en tiempo real que usa chatgpt.com
para ir mostrando la respuesta, y se cuelga esperandola. Lo que si
funciona es "--start-minimized": la ventana arranca minimizada (nunca se
ve ni roba el foco) pero Chromium la sigue tratando como una ventana
normal para efectos de renderizado, asi que no se cuelga."""

import atexit
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

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


def preguntar_internet(pregunta: str) -> str:
    """Busca la respuesta a una pregunta en internet (via ChatGPT) cuando
    el conocimiento propio no alcanza -- informacion reciente, clima,
    noticias -- o cuando el usuario pide explicitamente buscar en
    internet o preguntarle a ChatGPT. Si preguntas algo relacionado poco
    despues de la pregunta anterior, se manda como continuacion de esa
    misma conversacion automaticamente -- no hace falta repetir el
    contexto.

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


TOOLS = [preguntar_internet]
