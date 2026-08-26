"""Skill: control de volumen maestro del sistema (pycaw). Adaptado de
fase6_asistente.py:192-220, agregando anotaciones de tipo y devolviendo
texto de confirmacion (ver nota en skills/apps.py sobre por que)."""

import comtypes
from pycaw.pycaw import AudioUtilities


def _volumen_maestro():
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # ya estaba inicializado en este hilo, no pasa nada
    return AudioUtilities.GetSpeakers().EndpointVolume


def obtener_volumen() -> int:
    """Volumen maestro actual del sistema, 0-100. No es una tool en si
    misma -- la usan las demas funciones de este modulo."""
    return round(_volumen_maestro().GetMasterVolumeLevelScalar() * 100)


def establecer_volumen(porcentaje: int) -> str:
    """Fija el volumen maestro del sistema a un porcentaje exacto.

    Args:
        porcentaje: nivel de volumen deseado, de 0 a 100.
    """
    porcentaje = max(0, min(100, porcentaje))
    _volumen_maestro().SetMasterVolumeLevelScalar(porcentaje / 100, None)
    return f"Volumen en {porcentaje} por ciento."


def subir_volumen(paso: int = 10) -> str:
    """Sube el volumen maestro del sistema una cantidad relativa.

    Args:
        paso: cuanto subir el volumen, en puntos porcentuales.
    """
    establecer_volumen(obtener_volumen() + paso)
    return f"Volumen en {obtener_volumen()} por ciento."


def bajar_volumen(paso: int = 10) -> str:
    """Baja el volumen maestro del sistema una cantidad relativa.

    Args:
        paso: cuanto bajar el volumen, en puntos porcentuales.
    """
    establecer_volumen(obtener_volumen() - paso)
    return f"Volumen en {obtener_volumen()} por ciento."


def silenciar(mute: bool = True) -> str:
    """Silencia o reactiva el audio del sistema.

    Args:
        mute: True para silenciar, False para reactivar el sonido.
    """
    _volumen_maestro().SetMute(1 if mute else 0, None)
    return "Silenciado." if mute else "Sonido reactivado."


def obtener_volumen_actual() -> str:
    """Informa el volumen maestro actual del sistema, cuando el usuario
    pregunta que volumen tiene puesto (no lo cambia)."""
    return f"El volumen esta en {obtener_volumen()} por ciento."


TOOLS = [establecer_volumen, subir_volumen, bajar_volumen, silenciar, obtener_volumen_actual]
