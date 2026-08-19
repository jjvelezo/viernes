"""Fase 4 - Cursor virtual: mueve el cursor real del sistema (prueba por teclado, sin cámara)."""

import ctypes
import sys

import pywintypes
import win32api
import win32con
import win32gui

MANO_CURSOR = "Right"  # única mano que mueve el cursor por ahora
UMBRAL_SNAP = 20  # píxeles de cercanía a un borde de monitor para activar el snap tipo Windows

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


def obtener_ventana_bajo_cursor():
    """(hwnd, titulo) de la ventana bajo el cursor, o (None, None) si no hay ninguna."""
    x, y = win32gui.GetCursorPos()
    hwnd = win32gui.WindowFromPoint((x, y))
    if not hwnd:
        return None, None

    hwnd_raiz = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    titulo = win32gui.GetWindowText(hwnd_raiz)
    return hwnd_raiz, titulo


def pedir_entero(mensaje):
    while True:
        valor = input(mensaje).strip()
        try:
            return int(valor)
        except ValueError:
            print("  Eso no es un número entero válido, intenta de nuevo.")


def menu():
    print("=== Fase 4: mover cursor (prueba manual sin cámara) ===")
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
