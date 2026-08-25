"""Fase 10 - Asistente por voz, push-to-talk: reemplaza la wake word
("Viernes", fase8/fase9) por mantener apretada una tecla global (F9 por
defecto, TECLA_PTT) mientras se habla. Se sueltea la tecla, deja de grabar,
transcribe y ejecuta. Un ícono en la bandeja del sistema (system tray)
indica el estado — gris = esperando, azul = escuchando, naranja =
procesando — como la lucecita de Alexa.

Por qué el cambio: la wake word ya andaba (ver fase8_wakeword.py), pero el
feedback fue que preferían control manual explícito antes que confiar en
que "Viernes" siempre se reconozca bien. fase8/fase9 quedan como están
(funcionan, son el checkpoint de la wake word) — este archivo es la nueva
forma principal de usar el asistente por voz, no un reemplazo que los borre.

Reusa fase9_asistente_voz.interpretar_y_ejecutar() (mismo dispatcher fijo
de comandos) y fase6_asistente.hablar() para la respuesta — no duplica esa
lógica. Solo necesita el modelo "small" de Whisper (nada de modelo liviano
para wake word, porque ya no hay wake word que escuchar todo el tiempo).

Nota: el hook de teclado global (librería `keyboard`) puede no dispararse
si la ventana en foco corre como administrador y este script no — es una
restricción de Windows (UIPI), no un bug de acá."""

import sys
import threading

import numpy as np
import pystray
import sounddevice as sd
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw
import keyboard

import fase6_asistente as asistente
from fase9_asistente_voz import interpretar_y_ejecutar

MUESTREO_HZ = 16000
MODELO_COMANDO = "small"
TECLA_PTT = "f9"  # cambiar acá si F9 molesta con algo


def _icono_circulo(color):
    imagen = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).ellipse((8, 8, 56, 56), fill=color)
    return imagen


ICONO_INACTIVO = _icono_circulo((128, 128, 128, 255))  # gris: esperando que apretes la tecla
ICONO_ESCUCHANDO = _icono_circulo((30, 144, 255, 255))  # azul: grabando mientras mantenés la tecla
ICONO_PROCESANDO = _icono_circulo((255, 140, 0, 255))  # naranja: transcribiendo/ejecutando


class _EstadoGrabacion:
    """Estado compartido entre el callback de audio (hilo de sounddevice) y
    los handlers de teclado (hilo de la librería `keyboard`)."""

    def __init__(self):
        self.grabando = False
        self.tecla_abajo = False
        self.fragmentos = []

    def callback_audio(self, indata, frames, time_info, status):
        if self.grabando:
            self.fragmentos.append(indata.copy())


def _procesar_comando(audio, modelo):
    if audio.size < MUESTREO_HZ * 0.3:  # menos de ~0.3s: probablemente un toque sin querer
        print("  (grabación muy corta, la ignoro)\n")
        return

    segmentos, _info = modelo.transcribe(audio, language="es")
    texto = " ".join(segmento.text.strip() for segmento in segmentos)
    if not texto:
        print("  No entendí nada.\n")
        return

    print(f'  Comando: "{texto}"')
    respuesta = interpretar_y_ejecutar(texto)
    if respuesta:
        print(f'  Respuesta: "{respuesta}"\n')
        asistente.hablar(respuesta)
    else:
        print("  (comando no reconocido, o no necesita respuesta hablada)\n")


def setup(icon):
    icon.visible = True
    print(f'=== Fase 10: asistente push-to-talk (mantené "{TECLA_PTT.upper()}" para hablar) ===')
    print(f"  Cargando modelo '{MODELO_COMANDO}'...")
    modelo = WhisperModel(MODELO_COMANDO, device="cpu", compute_type="int8")
    print(f'  Listo. Mantené "{TECLA_PTT.upper()}" apretada para hablar (revisá el ícono en la bandeja).')

    estado = _EstadoGrabacion()
    stream = sd.InputStream(
        samplerate=MUESTREO_HZ, channels=1, dtype="float32", callback=estado.callback_audio
    )
    stream.start()

    def al_presionar(_evento):
        if estado.tecla_abajo:
            return  # repetición del sistema operativo mientras se mantiene apretada, ignorar
        estado.tecla_abajo = True
        estado.fragmentos = []
        estado.grabando = True
        icon.icon = ICONO_ESCUCHANDO
        print("  Escuchando...")

    def al_soltar(_evento):
        if not estado.tecla_abajo:
            return
        estado.tecla_abajo = False
        estado.grabando = False
        icon.icon = ICONO_PROCESANDO
        audio = np.concatenate(estado.fragmentos, axis=0).flatten() if estado.fragmentos else np.zeros(0, dtype="float32")
        try:
            _procesar_comando(audio, modelo)
        finally:
            icon.icon = ICONO_INACTIVO

    keyboard.on_press_key(TECLA_PTT, al_presionar)
    keyboard.on_release_key(TECLA_PTT, al_soltar)


def _salir(icon, _item):
    keyboard.unhook_all()
    icon.stop()


def main():
    icon = pystray.Icon(
        "viernes",
        icon=ICONO_INACTIVO,
        title="Viernes (push-to-talk)",
        menu=pystray.Menu(pystray.MenuItem("Salir", _salir)),
    )
    icon.run(setup=setup)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("El resto del proyecto asume Windows.", file=sys.stderr)
    main()
