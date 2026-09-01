"""Ventana de chat del agente en WebView (pywebview + WebView2).

Reemplazo de core/chat.py (customtkinter). Misma idea de siempre: burbujas,
switch "responder por voz", cancelar con contador de generación. La UI ahora
es HTML/CSS (core/chat_ui/index.html) renderizada por el WebView2 que ya
trae Windows. La ventana es negra y un poco translúcida (alpha uniforme
vía SetLayeredWindowAttributes — el blur real de DWM no atraviesa WebView2).

El motor y las skills NO cambian: el texto va a motor_llm.ejecutar_turno
igual que el push-to-talk.

Hilos: pywebview corre su loop en el hilo principal (webview.start() en
main.py). Los métodos de `_Api` los invoca pywebview desde su propio hilo;
el trabajo pesado (Gemma) se tira a un thread aparte y la respuesta vuelve
a la página con window.evaluate_js, que es thread-safe.
"""

from __future__ import annotations

import ctypes
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

import webview

import skills
from core import motor_llm
from core.voz import hablar

_LOG = logging.getLogger("agente.chat")

_HTML = Path(__file__).with_name("chat_ui") / "index.html"

ANCHO, ALTO = 440, 640
MARGEN = 28  # px desde el borde inferior-derecho

# Translucidez de TODA la ventana (0 = invisible, 255 = opaca). El blur real
# de DWM (SetWindowCompositionAttribute) no funciona detrás de un WebView2
# —la superficie de Chromium tapa todo—, así que se usa alpha uniforme de
# ventana (SetLayeredWindowAttributes): negro pero se ve un poco lo de atrás.
ALPHA_VENTANA = 232

# texto de estado (lo muestra la página) -> color del puntito
_ESTADO_PTT = {
    "escuchando": ("Escuchando…", "#4c8dff"),
    "procesando": ("Transcribiendo lo que dijiste…", "#f5a623"),
    "inactivo": ("", "#6b7078"),  # en reposo no muestra texto, solo el puntito
}


# --------------------------------------------------------------------- DWM
def _hwnd_de_titulo(titulo: str, intentos: int = 40):
    """Busca el HWND de la ventana por título (reintenta un rato porque el
    WebView2 tarda un pelín en registrar la ventana top-level)."""
    import time

    for _ in range(intentos):
        h = ctypes.windll.user32.FindWindowW(None, titulo)
        if h:
            return h
        time.sleep(0.05)
    return 0


def _translucidez(hwnd: int, alpha: int = ALPHA_VENTANA):
    """Hace toda la ventana un poco translúcida (alpha uniforme). Confiable en
    cualquier Windows, a diferencia del blur de DWM que no atraviesa WebView2."""
    try:
        u = ctypes.windll.user32
        GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x00080000, 0x2
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED)
        u.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
    except Exception:
        pass

    # Matar el marco/sombra no-cliente que DWM dibuja en la ventana frameless
    # (se veía como un recuadro blanco de ~2px alrededor de todo).
    try:
        DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
        valor = ctypes.c_int(DWMNCRP_DISABLED)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_NCRENDERING_POLICY, ctypes.byref(valor), ctypes.sizeof(valor)
        )
    except Exception:
        pass


def _pos_abajo_derecha():
    # Sin SetProcessDPIAware: dejamos que pywebview/WebView2 manejen el DPI.
    u = ctypes.windll.user32
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    return sw - ANCHO - MARGEN, sh - ALTO - MARGEN - 40


def _js_args(args):
    return ", ".join(json.dumps(a, ensure_ascii=False) for a in args)


def _fijar_encima(hwnd: int, activo: bool):
    """Deja (o saca) la ventana siempre encima del resto (topmost).

    OJO: `ctypes.windll.user32.SetWindowPos` es un objeto COMPARTIDO en todo
    el proceso — pywebview también lo usa (winforms.move). No hay que tocarle
    `.argtypes`, rompe el arrastre de la ventana. En vez de eso paso los dos
    HWND ya como `c_void_p`: así el -1 (HWND_TOPMOST) se extiende bien a 64
    bits sin tener que declarar la firma. El resto van como int por defecto."""
    try:
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(HWND_TOPMOST if activo else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


# --------------------------------------------------------------------- API
class _Api:
    """Lo que la página puede llamar (window.pywebview.api.*)."""

    def __init__(self):
        self._window = None
        self._hwnd = 0
        self._fijado = False
        self._gen = 0
        self._voz = True
        # los engancha main._arrancar cuando el push-to-talk está listo:
        # son las MISMAS funciones que dispara F9 (apretar / soltar).
        self._mic_iniciar = None
        self._mic_finalizar = None

    # ---- JS -> Python ----
    def mic(self, encender):
        """El botón de micrófono del chat: mismo flujo que F9. `encender`
        True = empezar a escuchar; False = cortar y procesar."""
        fn = self._mic_iniciar if encender else self._mic_finalizar
        if fn:
            fn()

    def pin(self, activo):
        """Fijar la ventana encima de todo (no se tapa al clickear otra app)."""
        self._fijado = bool(activo)
        if self._hwnd:
            _fijar_encima(self._hwnd, self._fijado)

    def ready(self):
        self.set_estado_ptt("inactivo")
        self.agregar(
            "asistente",
            "A tus órdenes. Escribime lo que necesites o mantené F9 para "
            "hablarme.\nAbro apps, manejo el volumen, controlo Spotify, leo "
            "la ventana activa y respondo preguntas.",
        )

    def set_voz(self, on):
        self._voz = bool(on)

    def cancelar(self):
        self._gen += 1  # invalida el resultado que venga del worker en curso
        self._emit("vrnPensando", False)
        self._emit("vrnSistema", "— cancelado —")
        self._emit("vrnReactivar")
        self.set_estado_ptt("inactivo")

    def enviar(self, texto):
        texto = (texto or "").strip()
        if not texto:
            return
        self._gen += 1
        gen = self._gen
        self.agregar("usuario", texto)
        self._emit("vrnPensando", True)
        threading.Thread(target=self._trabajar, args=(texto, gen), daemon=True).start()

    def minimizar(self):
        if self._window:
            self._window.minimize()

    def cerrar(self):
        if self._window:
            self._window.destroy()

    # ---- helpers (los usa también main.py) ----
    def agregar(self, quien, texto):
        self._emit("vrnAgregar", quien, texto, datetime.now().strftime("%H:%M"))

    def set_estado_ptt(self, nombre):
        self._emit("vrnEstado", *_ESTADO_PTT.get(nombre, _ESTADO_PTT["inactivo"]))

    # ---- interno ----
    def _emit(self, fn, *args):
        if not self._window:
            return
        try:
            self._window.evaluate_js(f"{fn}({_js_args(args)})")
        except Exception:
            pass

    def _trabajar(self, texto, gen):
        _LOG.info('Chat: "%s"', texto)
        try:
            respuesta, llamadas = motor_llm.ejecutar_turno(texto, skills.TOOLS)
            for ll in llamadas:
                _LOG.info("  Tool: %s(%s) -> %s", ll["tool"], ll["args"], ll["resultado"])
        except Exception as error:  # que un fallo no deje el chat trabado
            _LOG.exception("Error ejecutando el turno de chat")
            respuesta = f"Uy, algo falló: {error}"
        if not respuesta:
            respuesta = "Listo."
        _LOG.info('Respuesta chat: "%s"', respuesta)

        if gen != self._gen:
            return  # el usuario canceló o mandó otro mensaje
        self._emit("vrnPensando", False)
        self._emit("vrnReactivar")
        self.agregar("asistente", respuesta)
        self.set_estado_ptt("inactivo")
        if self._voz:
            threading.Thread(target=hablar, args=(respuesta,), daemon=True).start()


def crear():
    """Crea la ventana (todavía sin arrancar el loop). Devuelve (api, window).
    El caller hace webview.start(...) en el hilo principal."""
    api = _Api()
    x, y = _pos_abajo_derecha()
    ventana = webview.create_window(
        "Viernes",
        url=_HTML.as_uri(),
        js_api=api,
        width=ANCHO,
        height=ALTO,
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,  # se arrastra solo desde la cabecera (.pywebview-drag-region)
        resizable=True,
        min_size=(380, 460),
        background_color="#16171c",
        transparent=False,
        hidden=True,  # arranca oculta: solo el ícono de bandeja
    )
    api._window = ventana

    hecho = {"v": False}

    def _preparar_ventana():
        if hecho["v"]:
            return
        hwnd = _hwnd_de_titulo("Viernes")
        if hwnd:
            hecho["v"] = True
            api._hwnd = hwnd
            _translucidez(hwnd)
            if api._fijado:  # por si la página pidió pin antes de tener hwnd
                _fijar_encima(hwnd, True)

    def _al_mostrar():
        _preparar_ventana()
        # cada vez que se abre desde la bandeja, re-aplicar el "encima de todo"
        # (mostrar la ventana puede resetear el estilo topmost).
        if api._hwnd and api._fijado:
            _fijar_encima(api._hwnd, True)

    # `loaded` (la página cargó) y `shown` (primer .show() desde la bandeja):
    # con cualquiera ya tenemos HWND para aplicar la translucidez.
    ventana.events.loaded += _preparar_ventana
    ventana.events.shown += _al_mostrar
    return api, ventana
