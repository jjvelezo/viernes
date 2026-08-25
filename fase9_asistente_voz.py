"""Fase 9 - Asistente por voz completo: junta fase8_wakeword.py (escuchar
"Viernes" + transcribir el comando) con fase6_asistente.py (ejecutar la
acción) — el pipeline completo de la Etapa 1 en
privado/plan_asistente_ia.md:

    wake word "Viernes" -> Whisper -> ¿comando conocido? -> ejecutar directo

Reusa ambos módulos por import en vez de duplicar su lógica; ninguno de los
dos (ni este archivo) toca fase1-4/main.py, así que el mouse gestual sigue
sin poder romperse por esto.

interpretar_y_ejecutar() es el dispatcher fijo de la Etapa 1: matchea el
texto transcripto contra palabras clave y llama a la función de
fase6_asistente.py que corresponda. Nada de LLM/Ollama todavía (eso es la
Etapa 2) — si el comando no matchea ninguna palabra clave, se lo ignora."""

import difflib
import sys
import unicodedata

from faster_whisper import WhisperModel

import fase6_asistente as asistente
import fase8_wakeword as wakeword

PALABRAS_ABRIR = ("abri", "abre", "abrime", "abrir")
PALABRAS_LEER = ("lee", "leeme", "leer")
SIMILITUD_MINIMA_CHATGPT = 0.7  # calibrado para no confundir palabras comunes como "chapa" (0.667) o "chateando" (0.5) con mishears reales de "chatgpt"


def _normalizar(texto):
    """Minúsculas, sin tildes y sin puntuación (misma idea que
    fase8_wakeword._normalizar, reimplementada acá para no acoplar este
    módulo a los internos "privados" del otro)."""
    texto = unicodedata.normalize("NFKD", texto.lower().strip())
    sin_tildes = "".join(c for c in texto if not unicodedata.combining(c))
    return "".join(c for c in sin_tildes if c.isalnum() or c.isspace())


def _quitar_prefijo(palabras_normalizadas, prefijos):
    """Si la primera palabra está en `prefijos`, la saca. Devuelve el
    resto unido (ej. el nombre de la app a abrir)."""
    if palabras_normalizadas and palabras_normalizadas[0] in prefijos:
        return " ".join(palabras_normalizadas[1:]).strip()
    return " ".join(palabras_normalizadas).strip()


def _menciona_chatgpt(normalizado, palabras):
    """"chatgpt"/"gpt" exacto es el camino rápido, pero Whisper suele
    garabadear "chatgpt" al ser una palabra rara en español (sale como
    "chagipiti", "chat de pt", etc.) — igual que pasaba con "viernes" en
    fase8_wakeword.py. Por eso también acepta parecidos difusos."""
    if "chatgpt" in normalizado or "gpt" in palabras:
        return True
    return any(
        difflib.SequenceMatcher(None, "chatgpt", palabra).ratio() >= SIMILITUD_MINIMA_CHATGPT
        for palabra in palabras
    )


def interpretar_y_ejecutar(texto_crudo):
    """Interpreta `texto_crudo` (lo que dijo la persona después de la wake
    word) y ejecuta la acción de fase6_asistente.py correspondiente.
    Devuelve un texto para decir en voz alta (confirmación o resultado), o
    None si no reconoció el comando o si la acción ya se explica sola
    (ej. leer_ventana_activa ya lee el archivo, no hace falta doble aviso)."""
    normalizado = _normalizar(texto_crudo)
    palabras = normalizado.split()
    if not palabras:
        return None

    try:
        if palabras[0] in PALABRAS_ABRIR:
            nombre = _quitar_prefijo(palabras, PALABRAS_ABRIR)
            if not nombre:
                return "¿Qué querés que abra?"
            asistente.abrir_app(nombre)
            return f"Abriendo {nombre}."

        if _menciona_chatgpt(normalizado, palabras):
            return asistente.preguntar_chatgpt(texto_crudo.strip())

        if palabras[0] in PALABRAS_LEER:
            asistente.leer_ventana_activa()
            return None

        if "volumen" in normalizado:
            if "subi" in normalizado or "sube" in normalizado:
                asistente.subir_volumen()
            elif "baja" in normalizado or "baji" in normalizado:
                asistente.bajar_volumen()
            elif "silenci" in normalizado or "mute" in normalizado or "muteal" in normalizado:
                asistente.silenciar(True)
            elif "activa" in normalizado:
                asistente.silenciar(False)
            else:
                return "¿Subo o bajo el volumen?"
            return f"Volumen en {asistente.obtener_volumen()} por ciento."
    except ValueError as error:
        return str(error)

    return None  # comando no reconocido: se ignora, ver Etapa 2 en el plan para manejar esto con un LLM


def bucle_principal():
    print('=== Fase 9: asistente por voz completo ("Viernes") ===')
    print(f"  Cargando modelo '{wakeword.MODELO_WAKE}' (siempre activo)...")
    modelo_wake = WhisperModel(wakeword.MODELO_WAKE, device="cpu", compute_type="int8")
    print(f"  Cargando modelo '{wakeword.MODELO_COMANDO}' (para el comando)...")
    modelo_comando = WhisperModel(wakeword.MODELO_COMANDO, device="cpu", compute_type="int8")
    print("  Listo.\n")

    while True:
        wakeword.escuchar_wake_word(modelo_wake)
        print("  ¡Wake word detectada!")
        texto = wakeword.escuchar_comando(modelo_comando)
        if not texto:
            print("  No entendí nada.\n")
            continue

        print(f'  Comando: "{texto}"')
        respuesta = interpretar_y_ejecutar(texto)
        if respuesta:
            print(f'  Respuesta: "{respuesta}"\n')
            asistente.hablar(respuesta)
        else:
            print("  (comando no reconocido, o no necesita respuesta hablada)\n")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("El resto del proyecto asume Windows.", file=sys.stderr)
    try:
        bucle_principal()
    except KeyboardInterrupt:
        print("\n  Cortado.")
