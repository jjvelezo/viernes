"""Fase 4 - Cursor virtual: mueve el cursor real del sistema (prueba por teclado, sin cámara)."""

import ctypes
import sys

import win32api
import win32con
import win32gui

MANO_CURSOR = "Right"  # única mano que mueve el cursor por ahora

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
