"""Memoria conversacional del agente.

El motor LLM no guarda estado entre turnos (cada `create_conversation` es una
charla nueva). Este modulo aporta ese estado: mantiene la conversacion en
curso, la persiste en `privado/chats/` y arma la lista de mensajes previos
que `motor_llm.ejecutar_turno` le antepone a cada turno.

Estrategia de contexto (hibrida, la que usan casi todas las herramientas del
rubro): los ultimos `ventana_turnos` turnos van verbatim; lo mas viejo se
comprime en un `resumen` de un parrafo que regenera el propio Gemma cuando la
ventana se desborda (ver `motor_llm._resumir`). Ademas la conversacion se
corta sola (empieza una nueva) por inactividad o por tamano, para no
sobresaturar al modelo chico.

Voz (F9) y ventana de chat comparten esta memoria: es un singleton de modulo
y ambos caminos entran por `motor_llm.ejecutar_turno`. Todo acceso va bajo
`_lock` porque las dos entradas viven en hilos distintos.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import config

_LOG = logging.getLogger("agente.memoria")

# Misma raiz que core/logs.py: privado/ ya esta gitignored.
CARPETA = Path(__file__).resolve().parent.parent.parent / "privado" / "chats"
_INDICE = CARPETA / "index.json"

_lock = threading.RLock()

# Conversacion actual (ver _nueva_estructura).
_actual: dict | None = None


def _cfg(clave, defecto):
    return config.obtener(f"memoria.{clave}", defecto)


def activa() -> bool:
    return bool(_cfg("activa", True))


def _ventana() -> int:
    return int(_cfg("ventana_turnos", 6))


# --------------------------------------------------------------- estructura
def _nuevo_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _nueva_estructura() -> dict:
    ahora = time.time()
    return {
        "id": _nuevo_id(),
        "inicio": ahora,
        "ultimo_turno": ahora,
        "titulo": "",
        "resumen": "",
        # total de intercambios del usuario en toda la charla (para el corte
        # por tamano y la etiqueta del selector): `turnos` se recorta al
        # comprimir, asi que no sirve para contar.
        "total_turnos": 0,
        # turnos verbatim que todavia no entraron al resumen:
        "turnos": [],  # [{"rol": "usuario"|"asistente", "texto": str, "hora": "HH:MM"}]
    }


def _cargar_actual() -> dict:
    global _actual
    if _actual is None:
        _actual = _nueva_estructura()
    return _actual


def iniciar() -> None:
    """Prepara la carpeta y la conversacion en curso. Llamar al arrancar
    (opcional: si no, se crea sola en el primer turno)."""
    with _lock:
        try:
            CARPETA.mkdir(parents=True, exist_ok=True)
        except Exception:
            _LOG.debug("no se pudo crear %s", CARPETA, exc_info=True)
        _cargar_actual()


# ------------------------------------------------------------- persistencia
def _escribir_json(ruta: Path, datos) -> None:
    """Escritura atomica: tmp + os.replace, para no corromper si el proceso
    muere a mitad."""
    try:
        CARPETA.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(ruta.suffix + ".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ruta)
    except Exception:
        _LOG.warning("no se pudo escribir %s", ruta, exc_info=True)


def _leer_json(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return defecto


def _persistir(conv: dict) -> None:
    if not conv["turnos"] and not conv["resumen"]:
        return  # conversacion vacia: no ensucia el disco ni el indice
    _escribir_json(CARPETA / f"{conv['id']}.json", conv)
    _actualizar_indice(conv)


def _actualizar_indice(conv: dict) -> None:
    indice = _leer_json(_INDICE, [])
    if not isinstance(indice, list):
        indice = []
    meta = {
        "id": conv["id"],
        "titulo": conv["titulo"] or "(sin titulo)",
        "fecha": conv["ultimo_turno"],
        "n_turnos": _contar_turnos(conv),
    }
    indice = [e for e in indice if e.get("id") != conv["id"]]
    indice.append(meta)
    indice.sort(key=lambda e: e.get("fecha", 0), reverse=True)
    _escribir_json(_INDICE, indice)


def _contar_turnos(conv: dict) -> int:
    """Intercambios del usuario en TODA la charla (no solo los verbatim, que
    se recortan al comprimir). `total_turnos` no existe en conversaciones
    guardadas antes de este campo -> se estima con lo que haya."""
    total = conv.get("total_turnos")
    if total is not None:
        return total
    return sum(1 for t in conv["turnos"] if t["rol"] == "usuario")


# --------------------------------------------------------- API para el motor
def _texto(rol: str, texto: str) -> dict:
    # litert-lm usa "assistant" en su capa JSON (ver conversation.py: los
    # chunks del modelo salen como {"role": "assistant", ...}). Pasar "model"
    # hacia que el preface se ignorara y el modelo no viera el contexto.
    role = "assistant" if rol == "asistente" else "user"
    return {"role": role, "content": [{"type": "text", "text": texto}]}


def mensajes_previos() -> list[dict]:
    """Historial que se antepone al turno nuevo en create_conversation(messages=...).
    Lista vacia si la memoria esta apagada -> comportamiento historico."""
    if not activa():
        return []
    with _lock:
        conv = _cargar_actual()
        previos: list[dict] = []
        if conv["resumen"]:
            previos.append(_texto(
                "usuario",
                "Contexto de la conversacion hasta ahora (resumen): " + conv["resumen"],
            ))
            previos.append(_texto("asistente", "Entendido, sigo con eso en mente."))
        for t in conv["turnos"][-_ventana() * 2:]:
            previos.append(_texto(t["rol"], t["texto"]))
        return previos


def registrar(texto_usuario: str, respuesta: str) -> None:
    """Agrega el par usuario/asistente a la conversacion en curso y la
    persiste. No hace la compresion (la dispara motor_llm si
    necesita_resumen())."""
    if not activa():
        return
    with _lock:
        conv = _cargar_actual()
        hora = datetime.now().strftime("%H:%M")
        if not conv["titulo"] and texto_usuario:
            conv["titulo"] = texto_usuario.strip()[:40]
        conv["turnos"].append({"rol": "usuario", "texto": texto_usuario, "hora": hora})
        if respuesta:
            conv["turnos"].append({"rol": "asistente", "texto": respuesta, "hora": hora})
        conv["total_turnos"] = conv.get("total_turnos", 0) + 1
        conv["ultimo_turno"] = time.time()
        _persistir(conv)


def necesita_resumen() -> bool:
    """True si hay turnos verbatim mas alla de la ventana esperando comprimirse."""
    if not activa():
        return False
    with _lock:
        # *2 porque cada "turno" de usuario son dos entradas (usuario + asistente)
        return len(_cargar_actual()["turnos"]) > _ventana() * 2


def turnos_a_comprimir() -> list[dict]:
    """Los turnos viejos (fuera de la ventana) que hay que meter al resumen."""
    with _lock:
        conv = _cargar_actual()
        corte = len(conv["turnos"]) - _ventana() * 2
        return list(conv["turnos"][:max(0, corte)])


def resumen_actual() -> str:
    with _lock:
        return _cargar_actual()["resumen"]


def aplicar_resumen(nuevo_resumen: str) -> None:
    """Guarda el resumen regenerado y descarta del buffer verbatim los turnos
    que ya quedaron representados en el."""
    if not nuevo_resumen:
        return
    with _lock:
        conv = _cargar_actual()
        corte = len(conv["turnos"]) - _ventana() * 2
        if corte > 0:
            conv["turnos"] = conv["turnos"][corte:]
        conv["resumen"] = nuevo_resumen.strip()
        _LOG.info(
            "memoria: conversacion comprimida -> resumen de %d chars, quedan %d turnos verbatim. Resumen: %s",
            len(conv["resumen"]), len(conv["turnos"]), conv["resumen"],
        )
        _persistir(conv)


def rotar_si_corresponde() -> None:
    """Al inicio de cada turno: arranca una conversacion nueva si paso mucho
    tiempo sin hablar o si la actual ya crecio demasiado."""
    if not activa():
        return
    global _actual
    with _lock:
        conv = _cargar_actual()
        if not conv["turnos"]:
            return
        inactividad = float(_cfg("inactividad_min", 15)) * 60
        max_turnos = int(_cfg("max_turnos", 30))
        vieja = (time.time() - conv["ultimo_turno"]) > inactividad
        grande = _contar_turnos(conv) >= max_turnos and not conv.get("reanudada")
        if vieja or grande:
            mins = (time.time() - conv["ultimo_turno"]) / 60
            _LOG.info(
                "memoria: nueva conversacion (%s) -- se guardo '%s' (%d turnos, %.0f min sin hablar)",
                "inactividad" if vieja else "tamano",
                conv["titulo"] or conv["id"], _contar_turnos(conv), mins,
            )
            _persistir(conv)
            _actual = _nueva_estructura()


# ----------------------------------------------------------- API para la UI
def listar() -> list[dict]:
    """[{id, titulo, fecha, n_turnos}] para el selector, mas nueva primero."""
    with _lock:
        indice = _leer_json(_INDICE, [])
        return indice if isinstance(indice, list) else []


def cargar(id_conv: str) -> list[dict]:
    """Hace de `id_conv` la conversacion actual y devuelve sus turnos para
    que la ventana los repinte. Persiste antes la que estaba en curso."""
    global _actual
    with _lock:
        datos = _leer_json(CARPETA / f"{id_conv}.json", None)
        if not isinstance(datos, dict) or "turnos" not in datos:
            raise ValueError("No pude abrir esa conversacion.")
        if _actual is not None:
            _persistir(_actual)
        datos.setdefault("resumen", "")
        datos.setdefault("titulo", "")
        datos.setdefault(
            "total_turnos",
            sum(1 for t in datos["turnos"] if t.get("rol") == "usuario"),
        )
        # Al reabrir una charla a mano no se corta por tamano: el usuario la
        # eligio a proposito y quiere seguirla. El resumen igual mantiene el
        # contexto acotado. Solo la deja atras si la abandona (inactividad).
        datos["reanudada"] = True
        datos["ultimo_turno"] = time.time()
        _actual = datos
        return list(datos["turnos"])


def nueva() -> None:
    """Fuerza una conversacion nueva (boton manual del chat)."""
    global _actual
    with _lock:
        if _actual is not None:
            _persistir(_actual)
        _actual = _nueva_estructura()


def turnos_actuales() -> list[dict]:
    """Turnos de la conversacion en curso (para repintar al abrir la ventana)."""
    with _lock:
        return list(_cargar_actual()["turnos"])
