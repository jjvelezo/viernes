"""Skill: control de volumen maestro del sistema (pycaw). Reescrita de un
prototipo previo, agregando anotaciones de tipo y devolviendo un texto de
confirmacion para que el asistente lo diga en voz alta."""

import comtypes
from pycaw.pycaw import AudioUtilities

# Se cachea el endpoint de audio: obtenerlo (AudioUtilities.GetSpeakers())
# activa COM y enumera dispositivos cada vez, y "subir/bajar volumen" lo
# necesitaba varias veces por llamada. Si el dispositivo de audio por defecto
# cambia (auriculares, etc.) el objeto cacheado queda invalido -> _reintentar
# lo detecta, lo descarta y lo vuelve a pedir una vez.
_endpoint = None


def _volumen_maestro():
    global _endpoint
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # ya estaba inicializado en este hilo, no pasa nada
    if _endpoint is None:
        _endpoint = AudioUtilities.GetSpeakers().EndpointVolume
    return _endpoint


def _reintentar(operacion):
    """Ejecuta operacion(endpoint). Si el endpoint cacheado quedo invalido
    (cambio el dispositivo de audio por defecto), lo descarta y reintenta."""
    global _endpoint
    try:
        return operacion(_volumen_maestro())
    except (OSError, comtypes.COMError):
        _endpoint = None
        return operacion(_volumen_maestro())


def obtener_volumen() -> int:
    """Volumen maestro actual del sistema, 0-100. No es una tool en si
    misma -- la usan las demas funciones de este modulo."""
    return round(_reintentar(lambda v: v.GetMasterVolumeLevelScalar()) * 100)


def establecer_volumen(porcentaje: int) -> str:
    """Fija el volumen maestro del sistema a un porcentaje exacto.

    Args:
        porcentaje: nivel de volumen deseado, de 0 a 100.
    """
    porcentaje = max(0, min(100, porcentaje))
    _reintentar(lambda v: v.SetMasterVolumeLevelScalar(porcentaje / 100, None))
    return f"Volumen en {porcentaje} por ciento."


def _ajustar_relativo(paso: int) -> int:
    """Lee, calcula y fija el volumen con un solo acceso al endpoint."""
    def _op(v):
        actual = round(v.GetMasterVolumeLevelScalar() * 100)
        nuevo = max(0, min(100, actual + paso))
        v.SetMasterVolumeLevelScalar(nuevo / 100, None)
        return nuevo
    return _reintentar(_op)


def subir_volumen(paso: int = 10) -> str:
    """Sube el volumen maestro del sistema una cantidad relativa.

    Args:
        paso: cuanto subir el volumen, en puntos porcentuales.
    """
    return f"Volumen en {_ajustar_relativo(paso)} por ciento."


def bajar_volumen(paso: int = 10) -> str:
    """Baja el volumen maestro del sistema una cantidad relativa.

    Args:
        paso: cuanto bajar el volumen, en puntos porcentuales.
    """
    return f"Volumen en {_ajustar_relativo(-paso)} por ciento."


def silenciar(mute: bool = True) -> str:
    """Silencia o reactiva el audio del sistema.

    Args:
        mute: True para silenciar, False para reactivar el sonido.
    """
    _reintentar(lambda v: v.SetMute(1 if mute else 0, None))
    return "Silenciado." if mute else "Sonido reactivado."


def obtener_volumen_actual() -> str:
    """Informa el volumen maestro actual del sistema, cuando el usuario
    pregunta que volumen tiene puesto (no lo cambia)."""
    return f"El volumen esta en {obtener_volumen()} por ciento."


TOOLS = [establecer_volumen, subir_volumen, bajar_volumen, silenciar, obtener_volumen_actual]
