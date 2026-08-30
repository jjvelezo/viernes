"""Skill: hora y fecha actual. Sin esto el LLM podria inventar la hora --
cada turno es una conversacion nueva sin estado (ver core/motor_llm.py),
el modelo no tiene reloj propio ni sabe que dia es hoy."""

from datetime import datetime

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def obtener_hora_y_fecha() -> str:
    """Devuelve la hora y la fecha actual de la PC. Usar siempre que el
    usuario pregunte que hora es, que dia es, o similar -- nunca inventar
    la hora, el modelo no tiene reloj propio."""
    ahora = datetime.now()
    dia_semana = DIAS[ahora.weekday()]
    mes = MESES[ahora.month - 1]
    return f"Son las {ahora.strftime('%H:%M')} del {dia_semana} {ahora.day} de {mes} de {ahora.year}."


TOOLS = [obtener_hora_y_fecha]
