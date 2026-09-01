"""Cursor virtual: mueve el cursor y las ventanas reales del sistema vía
pywin32 (sin dependencia de cámara/MediaPipe). Ejecutado solo tiene un
menú por teclado para probar sin cámara."""

import ctypes
import sys

import pywintypes
import win32api
import win32con
import win32gui

MANO_CURSOR = "Right"  # mano por defecto para el cursor cuando ninguna mano está pellizcando
UMBRAL_SNAP = 20  # píxeles de cercanía a un borde de monitor para activar el snap tipo Windows
TAMANO_MINIMO = 100  # ancho/alto mínimo (px) al redimensionar, para no colapsar la ventana a 0

# Declara el proceso DPI-aware para que las coordenadas de pantalla
# coincidan con los píxeles reales (evita desalineación con pantallas escaladas).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def obtener_resolucion_pantalla():
    ancho = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    alto = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    return ancho, alto


def obtener_limites_virtuales():
    """(x_min, y_min, ancho, alto) del área virtual de escritorio (todos los monitores)."""
    x_min = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    y_min = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    ancho = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    alto = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    return x_min, y_min, ancho, alto


def coordenadas_validas(x, y):
    ancho, alto = obtener_resolucion_pantalla()
    return 0 <= x < ancho and 0 <= y < alto


def mover_cursor(x, y):
    win32api.SetCursorPos((int(x), int(y)))


def mover_ventana(hwnd, x, y):
    """Mueve la ventana `hwnd` a (x, y) sin cambiar su tamaño actual.
    Lanza ValueError si el handle ya no corresponde a una ventana válida
    (por ejemplo, si el proceso se cerró mientras se arrastraba)."""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"El handle {hwnd} ya no corresponde a una ventana válida.")

    try:
        win32gui.SetWindowPos(
            hwnd,
            0,          # hwndInsertAfter, ignorado por SWP_NOZORDER
            int(x),
            int(y),
            0,          # cx, cy: ignorados por SWP_NOSIZE
            0,
            win32con.SWP_NOZORDER | win32con.SWP_NOSIZE,
        )
    except pywintypes.error as error:
        raise ValueError(f"No se pudo mover la ventana {hwnd}: {error}") from error


def cerrar_ventana(hwnd):
    """Pide cerrar la ventana `hwnd` (equivalente a hacer clic en la X:
    respeta diálogos de "guardar cambios" de la propia aplicación)."""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"El handle {hwnd} ya no corresponde a una ventana válida.")
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except pywintypes.error as error:
        raise ValueError(f"No se pudo cerrar la ventana {hwnd}: {error}") from error


def clic_izquierdo():
    """Simula un clic izquierdo del mouse en la posición actual del cursor."""
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def hacer_scroll(cantidad):
    """Simula el scroll del mouse en la posición actual del cursor.
    Positivo = arriba, negativo = abajo. No hace falta que sea múltiplo
    de 120 (WHEEL_DELTA): Windows acepta deltas chicos para scroll suave."""
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(cantidad), 0)


def redimensionar_ventana(hwnd, izquierda, arriba, derecha, abajo):
    """Mueve y redimensiona `hwnd` para que ocupe el rectángulo dado
    (en coordenadas de pantalla). El ancho/alto resultante nunca baja de
    TAMANO_MINIMO, para que la ventana no colapse si las manos se cruzan."""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"El handle {hwnd} ya no corresponde a una ventana válida.")

    ancho = max(int(derecha - izquierda), TAMANO_MINIMO)
    alto = max(int(abajo - arriba), TAMANO_MINIMO)

    try:
        win32gui.SetWindowPos(
            hwnd,
            0,
            int(izquierda),
            int(arriba),
            ancho,
            alto,
            win32con.SWP_NOZORDER,
        )
    except pywintypes.error as error:
        raise ValueError(f"No se pudo redimensionar la ventana {hwnd}: {error}") from error


def obtener_area_trabajo_monitor(x, y):
    """(izq, arriba, derecha, abajo) del área de trabajo (sin la barra de
    tareas) del monitor que contiene el punto (x, y). Necesario para que
    el snap use el tamaño del monitor correcto en setups multi-monitor."""
    monitor = win32api.MonitorFromPoint((int(x), int(y)), win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(monitor)
    return info["Work"]


def esta_maximizada(hwnd):
    # pywin32 no expone IsZoomed(); GetWindowPlacement() es el equivalente
    # correcto: placement[1] es el showCmd actual de la ventana.
    placement = win32gui.GetWindowPlacement(hwnd)
    return placement[1] == win32con.SW_SHOWMAXIMIZED


def restaurar_ventana(hwnd):
    """Saca la ventana `hwnd` del estado maximizado (sin reposicionarla)."""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"El handle {hwnd} ya no corresponde a una ventana válida.")
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except pywintypes.error as error:
        raise ValueError(f"No se pudo restaurar la ventana {hwnd}: {error}") from error


def aplicar_snap_si_corresponde(hwnd, x, y):
    """Si (x, y) está pegado a un borde del monitor, ajusta la ventana al
    estilo Aero Snap de Windows: arriba = maximizar, izquierda/derecha =
    mitad de pantalla. Si no está cerca de ningún borde no hace nada y
    retorna False; si aplicó un snap retorna True."""
    if not win32gui.IsWindow(hwnd):
        raise ValueError(f"El handle {hwnd} ya no corresponde a una ventana válida.")

    izq, arriba, derecha, abajo = obtener_area_trabajo_monitor(x, y)
    ancho, alto = derecha - izq, abajo - arriba

    try:
        if y <= arriba + UMBRAL_SNAP:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return True
        if x <= izq + UMBRAL_SNAP:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, 0, izq, arriba, ancho // 2, alto, win32con.SWP_NOZORDER)
            return True
        if x >= derecha - UMBRAL_SNAP:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, 0, izq + ancho // 2, arriba, ancho // 2, alto, win32con.SWP_NOZORDER)
            return True
    except pywintypes.error as error:
        raise ValueError(f"No se pudo aplicar el snap a la ventana {hwnd}: {error}") from error

    return False


def obtener_ventana_en_punto(x, y):
    """(hwnd, titulo) de la ventana en el punto de pantalla (x, y), o
    (None, None) si no hay ninguna. Sirve para consultar "¿qué ventana hay
    ahí?" en cualquier posición, no solo donde está el cursor real."""
    hwnd = win32gui.WindowFromPoint((int(x), int(y)))
    if not hwnd:
        return None, None

    # WindowFromPoint puede devolver un control interno (un botón, un
    # textbox); GetAncestor con GA_ROOT sube hasta la ventana de nivel
    # superior, que es la que de verdad nos interesa.
    hwnd_raiz = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    titulo = win32gui.GetWindowText(hwnd_raiz)
    return hwnd_raiz, titulo


def obtener_ventana_bajo_cursor():
    """(hwnd, titulo) de la ventana bajo el cursor real, o (None, None)."""
    x, y = win32gui.GetCursorPos()
    return obtener_ventana_en_punto(x, y)


def pedir_entero(mensaje):
    while True:
        valor = input(mensaje).strip()
        try:
            return int(valor)
        except ValueError:
            print("  Eso no es un número entero válido, intenta de nuevo.")


def menu():
    print("=== Cursor virtual: prueba manual sin cámara ===")
    ancho, alto = obtener_resolucion_pantalla()
    print(f"Resolución detectada: {ancho}x{alto}\n")

    while True:
        x = pedir_entero("  Posición X: ")
        y = pedir_entero("  Posición Y: ")

        if not coordenadas_validas(x, y):
            print(f"  Coordenadas fuera de rango. La pantalla va de (0, 0) a ({ancho - 1}, {alto - 1}).")
        else:
            mover_cursor(x, y)
            hwnd, titulo = obtener_ventana_bajo_cursor()

            if hwnd is None or not titulo:
                print(f"  Cursor movido a ({x}, {y}). No hay ninguna ventana con título ahí.")
            else:
                print(f"  Cursor movido a ({x}, {y}). Ventana bajo el cursor: \"{titulo}\" (hwnd={hwnd})")

        respuesta = input("\n¿Probar otra posición? (s/n): ").strip().lower()
        if respuesta != "s":
            break


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este script solo funciona en Windows (usa pywin32).", file=sys.stderr)
        sys.exit(1)
    menu()
