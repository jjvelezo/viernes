"""Skill: cerrar/minimizar ventanas. Usa win32gui, mismo tipo de llamadas
que ya usa mouse_gestual/cursor.py para el mouse gestual (agente/ no lo
importa -- se reescribe la parte minima que hace falta; ver
docs/decisiones.md sobre el principio de aislamiento).

cerrar_ventana_activa() cierra lo que tenga el foco de Windows en ese
instante -- eso NO es necesariamente la app que el usuario acaba de
nombrar (ej. si abrio Brave por voz pero seguia mirando la consola, el
foco se queda en la consola, no en Brave). Por eso existe tambien
cerrar_ventana(nombre), que busca por titulo -- es la que hay que usar
cuando el usuario nombra la app, y si hay ambiguedad (cero o varias
coincidencias) no cierra nada, devuelve la lista para que el usuario
aclare en vez de adivinar."""

import win32con
import win32gui


def _listar_ventanas():
    """[(hwnd, titulo)] de las ventanas visibles con titulo."""
    ventanas = []

    def _callback(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            ventanas.append((hwnd, win32gui.GetWindowText(hwnd)))
        return True

    win32gui.EnumWindows(_callback, None)
    return ventanas


def _buscar_ventanas_por_nombre(nombre):
    clave = nombre.strip().lower()
    return [(hwnd, titulo) for hwnd, titulo in _listar_ventanas() if clave in titulo.lower()]


def cerrar_ventana(nombre: str) -> str:
    """Cierra la ventana de una aplicacion por su nombre. Usar SIEMPRE que
    el usuario nombre la app que quiere cerrar (ej. "cerrame Brave",
    "cerra el Cursor"). Si hay cero o varias ventanas que coinciden con el
    nombre, no cierra nada -- avisa cuales encontro para que el usuario
    aclare, en vez de adivinar cual cerrar.

    Args:
        nombre: nombre de la aplicacion o parte del titulo de su ventana.
    """
    coincidencias = _buscar_ventanas_por_nombre(nombre)
    if not coincidencias:
        raise ValueError(f'No encontre ninguna ventana abierta de "{nombre}".')
    if len(coincidencias) > 1:
        titulos = ", ".join(f'"{titulo}"' for _hwnd, titulo in coincidencias)
        raise ValueError(f'Hay varias ventanas que coinciden con "{nombre}": {titulos}. ¿Cual cierro?')
    hwnd, titulo = coincidencias[0]
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return f"Cerrando {titulo}."


def cerrar_ventana_activa() -> str:
    """Cierra la ventana que esta activa/en foco en este momento. Usar
    SOLO cuando el usuario NO nombra ninguna app especifica (ej. "cerra
    esta ventana", "cerra lo que tengo abierto") -- si nombra una app,
    usar cerrar_ventana(nombre) en cambio."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        raise ValueError("No hay ninguna ventana activa para cerrar.")
    titulo = win32gui.GetWindowText(hwnd) or "la ventana"
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return f"Cerrando {titulo}."


def minimizar_ventana_activa() -> str:
    """Minimiza la ventana que esta activa/en foco en este momento."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        raise ValueError("No hay ninguna ventana activa para minimizar.")
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    return "Ventana minimizada."


TOOLS = [cerrar_ventana, cerrar_ventana_activa, minimizar_ventana_activa]
