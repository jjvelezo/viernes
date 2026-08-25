"""Fase 6 - Asistente: abrir apps, preguntar a ChatGPT, volumen, leer en voz
alta (prueba por teclado, sin voz todavía — eso es la Etapa 1 completa,
ver privado/plan_asistente_ia.md). Módulo aislado: no importa nada de
fase1-4/main.py ni es importado por ellos, así que no puede romper el
mouse gestual."""

import asyncio
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

import comtypes
import edge_tts
import win32gui
from playwright.sync_api import sync_playwright
from pycaw.pycaw import AudioUtilities

# --- Abrir aplicaciones ---------------------------------------------------

APPS_CONOCIDAS = {
    "bloc de notas": "notepad",
    "notepad": "notepad",
    "calculadora": "calc",
    "explorador": "explorer",
    "explorador de archivos": "explorer",
    "paint": "mspaint",
    "chrome": "chrome",
    "navegador": "chrome",
    "word": "winword",
    "excel": "excel",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify",
    "cmd": "cmd",
    "consola": "cmd",
    "panel de control": "control",
}


def abrir_app(nombre):
    """Abre la app `nombre` (nombre común en español, o directamente el
    ejecutable). Usa os.startfile, que resuelve tanto ejecutables en PATH
    (notepad, calc, explorer, ya están ahí) como los registrados en la
    clave "App Paths" del registro (chrome, winword, etc. quedan ahí
    aunque no estén en PATH) — sin pasar por una shell, para no arrastrar
    riesgo de inyección con nombres raros."""
    clave = nombre.strip().lower()
    ejecutable = APPS_CONOCIDAS.get(clave, clave)
    try:
        os.startfile(ejecutable)
    except OSError as error:
        raise ValueError(f'No se pudo abrir "{nombre}": {error}') from error


# --- Preguntar a ChatGPT (Playwright) ---------------------------------

PERFIL_NAVEGADOR = Path(__file__).resolve().parent / "privado" / "perfil_chatgpt"
PLACEHOLDER_CAJA_TEXTO = "Pregúntale a ChatGPT"  # visible en la UI en español; se puede mandar sin login
SELECTOR_TRANSCRIPCION = "[data-conversation-transcript]:visible"
TEXTO_ACEPTAR_COOKIES = "Aceptar todas"


INSTRUCCION_RESUMEN = (
    "Respondé de forma directa y concisa, sin rodeos, pero sin ser ambiguo"
    " ni omitir información importante de la respuesta — la respuesta se"
    " va a leer en voz alta."
)


def preguntar_chatgpt(pregunta, tiempo_limite_s=45):
    """Manda `pregunta` a chatgpt.com (en una ventana de Chromium con
    perfil persistente en privado/perfil_chatgpt/, gitignored) y devuelve
    la respuesta como texto plano. No hace falta estar logueado: chatgpt.com
    deja mandar mensajes sueltos de forma anónima. Le agrega
    INSTRUCCION_RESUMEN a todo pedido — esto está pensado para leerse en
    voz alta, no para mostrarse en pantalla, así que siempre pide
    respuestas cortas.

    Frágil por diseño: chatgpt.com no expone ids/roles estables para el
    mensaje del asistente (su build usa clases CSS hasheadas que cambian en
    cada deploy) — lo más estable que se encontró es
    `[data-conversation-transcript]` (el `<ol>` de la conversación), el
    placeholder de la caja de texto, y el botón "Detener la generación"
    (aria-label, se usa para saber cuándo terminó de responder — mucho más
    rápido y confiable que esperar a que el texto "deje de cambiar", que es
    lo que se probó primero). Si deja de andar, lo primero es abrir
    chatgpt.com a mano, mandar un mensaje, e inspeccionar el DOM de nuevo."""
    PERFIL_NAVEGADOR.mkdir(parents=True, exist_ok=True)
    pregunta_completa = f"{pregunta.strip()} {INSTRUCCION_RESUMEN}"

    with sync_playwright() as playwright:
        # Viewport explícito: por debajo de cierto ancho, chatgpt.com sirve
        # un layout "mobile" con otros ids para la caja de texto.
        contexto = playwright.chromium.launch_persistent_context(
            str(PERFIL_NAVEGADOR), headless=False, viewport={"width": 1400, "height": 900}
        )
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.goto("https://chatgpt.com", wait_until="domcontentloaded")

            boton_cookies = pagina.get_by_text(TEXTO_ACEPTAR_COOKIES, exact=True)
            if boton_cookies.count() > 0:
                try:
                    boton_cookies.first.click(timeout=3000)
                except Exception:
                    pass  # si no aparece a tiempo, no es crítico, se sigue igual

            caja = pagina.get_by_placeholder(PLACEHOLDER_CAJA_TEXTO)
            caja.click(timeout=tiempo_limite_s * 1000)
            caja.fill(pregunta_completa)
            pagina.keyboard.press("Enter")

            try:
                boton_detener = pagina.get_by_label("Detener la generación")
                boton_detener.wait_for(state="visible", timeout=5000)
                boton_detener.wait_for(state="hidden", timeout=tiempo_limite_s * 1000)
            except Exception:
                pagina.wait_for_timeout(2000)  # fallback simple si el botón no está donde se esperaba

            transcripcion = pagina.locator(SELECTOR_TRANSCRIPCION).first
            transcripcion.wait_for(timeout=tiempo_limite_s * 1000)
            texto_completo = transcripcion.inner_text()

            if not texto_completo:
                raise ValueError("ChatGPT no respondió a tiempo.")
            return _extraer_respuesta(texto_completo, pregunta_completa)
        finally:
            contexto.close()


def _extraer_respuesta(transcripcion_completa, pregunta):
    """La transcripción completa viene como "Tú dijiste: / <pregunta> /
    <etiqueta del modelo> / <respuesta>" separado por líneas en blanco.
    Devuelve solo <respuesta>."""
    partes = [parte.strip() for parte in transcripcion_completa.split("\n\n") if parte.strip()]
    if pregunta not in partes:
        return transcripcion_completa.strip()
    indice = partes.index(pregunta)
    # partes[indice + 1] es la etiqueta del modelo (ej. "ChatGPT Plus"), la respuesta viene después.
    return "\n\n".join(partes[indice + 2:]).strip()


# --- Control de volumen (pycaw) --------------------------------------------


def _volumen_maestro():
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # ya estaba inicializado en este hilo, no pasa nada
    return AudioUtilities.GetSpeakers().EndpointVolume


def obtener_volumen():
    """Volumen maestro actual, 0-100."""
    return round(_volumen_maestro().GetMasterVolumeLevelScalar() * 100)


def establecer_volumen(porcentaje):
    """Fija el volumen maestro a `porcentaje` (se recorta a 0-100)."""
    porcentaje = max(0, min(100, porcentaje))
    _volumen_maestro().SetMasterVolumeLevelScalar(porcentaje / 100, None)


def subir_volumen(paso=10):
    establecer_volumen(obtener_volumen() + paso)


def bajar_volumen(paso=10):
    establecer_volumen(obtener_volumen() - paso)


def silenciar(mute=True):
    _volumen_maestro().SetMute(1 if mute else 0, None)


# --- Leer en voz alta la ventana activa -------------------------------------

EXTENSIONES_LEGIBLES = {".txt", ".md", ".py", ".csv", ".json", ".log", ".ini", ".cfg", ".yaml", ".yml"}
CARPETAS_BUSQUEDA = [Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads"]
MAX_CARACTERES_LECTURA = 3000


VOZ_TTS = "es-MX-DaliaNeural"  # edge-tts, español de México, mujer — se
# probaron antes las nativas SAPI/OneCore (Sabina x2, Laura, Helena, todas
# robóticas) y edge-tts Salome (Colombia, tampoco gustó); Dalia es la
# siguiente candidata más natural. Costo: hace falta internet (llamada a
# la nube gratis de Microsoft por cada frase). Otras ya escuchadas si esta
# tampoco convence: "es-AR-ElenaNeural" (Argentina), "es-US-PalomaNeural".
RATE_TTS = "+15%"  # edge-tts habla lento por defecto; +25% resultó demasiado, +15% es el punto justo


def _reproducir_mp3(ruta):
    """Reproduce un mp3 con el driver MCI de Windows (mismo mecanismo que
    usa Windows Media Player) — sin dependencias nuevas además de
    edge-tts, que no sabe decodificar/reproducir audio por sí solo."""
    mci = ctypes.windll.winmm.mciSendStringW
    alias = "voz_asistente"
    mci(f'open "{ruta}" type mpegvideo alias {alias}', None, 0, None)
    mci(f"play {alias} wait", None, 0, None)
    mci(f"close {alias}", None, 0, None)


def hablar(texto):
    """Convierte `texto` a voz (edge-tts, VOZ_TTS a velocidad RATE_TTS) y
    lo reproduce. Genera un mp3 temporal y lo borra al terminar."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
        ruta_temp = Path(temporal.name)
    try:
        comunicador = edge_tts.Communicate(texto, VOZ_TTS, rate=RATE_TTS)
        asyncio.run(comunicador.save(str(ruta_temp)))
        _reproducir_mp3(ruta_temp)
    finally:
        ruta_temp.unlink(missing_ok=True)


SEPARADORES_TITULO = (" - ", ": ", " — ", " | ")


def _extraer_nombre_archivo(titulo):
    """De un título tipo "notas.txt - Bloc de notas" (o "notas.txt: Bloc de
    notas", que es lo que usa el Notepad de esta versión de Windows)
    devuelve "notas.txt". Frágil por diseño: solo funciona con apps que
    muestran el nombre del archivo al principio del título separado por
    alguno de SEPARADORES_TITULO — no funciona con navegadores ni apps que
    no lo hacen."""
    corte = len(titulo)
    for separador in SEPARADORES_TITULO:
        posicion = titulo.find(separador)
        if posicion != -1 and posicion < corte:
            corte = posicion
    return titulo[:corte].strip()


def _buscar_archivo_por_nombre(nombre):
    ruta = Path(nombre)
    if ruta.is_absolute():
        return ruta if ruta.is_file() else None
    for carpeta in CARPETAS_BUSQUEDA:
        candidato = carpeta / nombre
        if candidato.is_file():
            return candidato
    return None


def leer_ventana_activa():
    """Busca el archivo de texto asociado a la ventana en foco (a partir
    de su título) y lo lee en voz alta. Solo anda si el título muestra el
    nombre del archivo (Notepad, VSCode...) y el archivo está en
    Escritorio, Documentos o Descargas (búsqueda no recursiva). Devuelve
    la ruta leída."""
    hwnd = win32gui.GetForegroundWindow()
    titulo = win32gui.GetWindowText(hwnd)
    if not titulo:
        raise ValueError("No se pudo leer el título de la ventana activa.")

    nombre_archivo = _extraer_nombre_archivo(titulo)
    if Path(nombre_archivo).suffix.lower() not in EXTENSIONES_LEGIBLES:
        raise ValueError(f'La ventana activa ("{titulo}") no parece un archivo de texto legible.')

    ruta = _buscar_archivo_por_nombre(nombre_archivo)
    if ruta is None:
        raise ValueError(f'No encontré "{nombre_archivo}" en Escritorio, Documentos ni Descargas.')

    texto = ruta.read_text(encoding="utf-8", errors="ignore")[:MAX_CARACTERES_LECTURA]
    if not texto.strip():
        raise ValueError(f'"{ruta}" está vacío.')
    hablar(texto)
    return ruta


# --- Menú de prueba manual ---------------------------------------------------


def _menu_abrir_app():
    nombre = input("  Nombre de la app a abrir: ").strip()
    abrir_app(nombre)
    print(f'  Abriendo "{nombre}"...')


def _menu_chatgpt():
    pregunta = input("  Pregunta para ChatGPT: ").strip()
    print("  Esperando respuesta (se abre una ventana de Chrome)...")
    respuesta = preguntar_chatgpt(pregunta)
    print(f"\n  ChatGPT: {respuesta}\n")


def _menu_volumen():
    print(f"  Volumen actual: {obtener_volumen()}%")
    print("  s = subir 10, b = bajar 10, m = silenciar, a = activar sonido, o un número 0-100")
    entrada = input("  Acción: ").strip().lower()
    if entrada == "s":
        subir_volumen()
    elif entrada == "b":
        bajar_volumen()
    elif entrada == "m":
        silenciar(True)
    elif entrada == "a":
        silenciar(False)
    else:
        try:
            establecer_volumen(int(entrada))
        except ValueError:
            print("  Entrada inválida.")
            return
    print(f"  Volumen ahora: {obtener_volumen()}%")


def _menu_leer():
    print("  Cambiá a la ventana con el archivo a leer. Arranco en:")
    for cuenta in (3, 2, 1):
        print(f"    {cuenta}...")
        time.sleep(1)
    ruta = leer_ventana_activa()
    print(f'  Leyendo "{ruta}" en voz alta.')


def menu():
    print("=== Fase 6: asistente (prueba manual, sin voz todavía) ===")
    opciones = {
        "1": ("Abrir una aplicación", _menu_abrir_app),
        "2": ("Preguntarle a ChatGPT", _menu_chatgpt),
        "3": ("Control de volumen", _menu_volumen),
        "4": ("Leer la ventana activa en voz alta", _menu_leer),
    }
    while True:
        print()
        for clave, (etiqueta, _) in opciones.items():
            print(f"  {clave}. {etiqueta}")
        print("  q. Salir")
        eleccion = input("\nElegí una opción: ").strip().lower()
        if eleccion == "q":
            break
        accion = opciones.get(eleccion)
        if accion is None:
            print("  Opción inválida.")
            continue
        try:
            accion[1]()
        except ValueError as error:
            print(f"  Error: {error}")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este script solo funciona en Windows.", file=sys.stderr)
        sys.exit(1)
    menu()
