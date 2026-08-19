"""Fase 5 - Mouse virtual: la punta del índice de la mano derecha mueve el cursor real del sistema."""

import sys
import time
from collections import deque

import cv2
import mediapipe as mp
import pywintypes
import win32gui
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from fase2_landmarks import MODEL_PATH, HAND_CONNECTIONS, dibujar_landmarks
from fase3_gestos import EstadoDebounce, detectar_gesto
from fase4_cursor import (
    MANO_CURSOR,
    aplicar_snap_si_corresponde,
    esta_maximizada,
    mover_cursor,
    mover_ventana,
    obtener_limites_virtuales,
    obtener_ventana_bajo_cursor,
    restaurar_ventana,
)

INDICE_PUNTA = 8  # landmark de la punta del dedo índice

HISTORIAL_LEN = 5       # cantidad de posiciones a promediar para filtrar temblor
FACTOR_SUAVIZADO = 0.6  # suavizado exponencial sobre la posición filtrada (1.0 = sin suavizado)
# Margen de cada borde de la cámara que no mapea a pantalla: cuanto más alto,
# menos hay que mover la mano para llegar a los bordes/esquinas de la pantalla
# (la zona activa dentro del cuadro se achica). El de Y va más alto porque
# levantar la mano cerca del borde superior del encuadre es lo más incómodo
# y además donde MediaPipe pierde antes la detección (mano saliendo de cuadro).
MARGEN_X = 0.20
MARGEN_Y = 0.30

ALTURA_AGARRE = 20  # distancia (px) del cursor al borde superior de la ventana al agarrarla, simula agarrar la barra de título

# MediaPipe detecta la mano invertida respecto al frame espejado, por eso se voltea acá
VOLTEAR_HANDEDNESS = True


def mapear_con_margen(valor, margen):
    ajustado = (valor - margen) / (1 - 2 * margen)
    return min(max(ajustado, 0.0), 1.0)


def es_mano_derecha(label):
    if VOLTEAR_HANDEDNESS:
        label = "Right" if label == "Left" else "Left"
    return label == MANO_CURSOR


def main():
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: no se pudo abrir la cámara.", file=sys.stderr)
        sys.exit(1)

    x_min_pantalla, y_min_pantalla, ancho_pantalla, alto_pantalla = obtener_limites_virtuales()

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.7,
        min_hand_presence_confidence=0.7,
        min_tracking_confidence=0.7,
    )

    prev_time = time.time()
    cursor_x, cursor_y = None, None  # posición suavizada actual; None = sin cursor asignado aún
    historial_x = deque(maxlen=HISTORIAL_LEN)
    historial_y = deque(maxlen=HISTORIAL_LEN)

    # Arrastre de ventanas con pellizco: máquina de estados de 2 posiciones
    estado_pellizco = EstadoDebounce()  # mismo debounce que fase3_gestos, para no engancharse/soltarse por temblor
    estado_arrastre = "sin_agarre"  # "sin_agarre" | "agarrado"
    hwnd_agarrado, titulo_agarrado = None, None
    offset_x, offset_y = 0, 0  # distancia entre el cursor y la esquina de la ventana al agarrarla

    with vision.HandLandmarker.create_from_options(options) as landmarker:
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print("Error: no se pudo leer el frame de la cámara.", file=sys.stderr)
                    break

                frame = cv2.flip(frame, 1)  # efecto espejo, más intuitivo para controlar el cursor

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                timestamp_ms = int(time.time() * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                h, w, _ = frame.shape
                mano_derecha_detectada = False
                gesto_actual = "NINGUNO"

                for hand_landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    dibujar_landmarks(frame, hand_landmarks, w, h)

                    label = handedness[0].category_name
                    if not es_mano_derecha(label):
                        continue

                    mano_derecha_detectada = True
                    gesto_actual = detectar_gesto(hand_landmarks)
                    punta_indice = hand_landmarks[INDICE_PUNTA]

                    historial_x.append(punta_indice.x)
                    historial_y.append(punta_indice.y)
                    x_filtrado = sum(historial_x) / len(historial_x)
                    y_filtrado = sum(historial_y) / len(historial_y)

                    x_ajustado = mapear_con_margen(x_filtrado, MARGEN_X)
                    y_ajustado = mapear_con_margen(y_filtrado, MARGEN_Y)

                    objetivo_x = x_min_pantalla + x_ajustado * ancho_pantalla
                    objetivo_y = y_min_pantalla + y_ajustado * alto_pantalla

                    if cursor_x is None:
                        cursor_x, cursor_y = objetivo_x, objetivo_y
                    else:
                        cursor_x += (objetivo_x - cursor_x) * FACTOR_SUAVIZADO
                        cursor_y += (objetivo_y - cursor_y) * FACTOR_SUAVIZADO

                    mover_cursor(cursor_x, cursor_y)

                    px, py = int(punta_indice.x * w), int(punta_indice.y * h)
                    cv2.circle(frame, (px, py), 10, (0, 255, 255), -1)

                if not mano_derecha_detectada:
                    cursor_x, cursor_y = None, None
                    historial_x.clear()
                    historial_y.clear()

                gesto_confirmado = estado_pellizco.actualizar(gesto_actual)

                # El gesto confirmado (con debounce) manda si seguimos "agarrados" o no.
                # cursor_x puede ser None por 1-2 frames sueltos aunque el pellizco siga
                # sostenido (tracking momentáneamente perdido por movimiento rápido/borroso):
                # eso NO debe soltar la ventana, solo significa "no hay posición nueva este
                # frame". Soltar de verdad depende únicamente de gesto_confirmado.
                if gesto_confirmado == "PELLIZCO":
                    if estado_arrastre == "sin_agarre" and cursor_x is not None:
                        hwnd_bajo, titulo_bajo = obtener_ventana_bajo_cursor()
                        if hwnd_bajo is not None and titulo_bajo:
                            try:
                                if esta_maximizada(hwnd_bajo):
                                    restaurar_ventana(hwnd_bajo)

                                # Como en Windows: no importa dónde caiga el pellizco dentro
                                # de la ventana, se agarra siempre "por la barra de título"
                                # -> el cursor queda centrado en X y cerca del borde superior,
                                # y la ventana salta ahí de una para que se sienta consistente.
                                izq, arriba, derecha, _ = win32gui.GetWindowRect(hwnd_bajo)
                                ancho_ventana = derecha - izq
                                offset_x = ancho_ventana / 2
                                offset_y = ALTURA_AGARRE
                                mover_ventana(hwnd_bajo, cursor_x - offset_x, cursor_y - offset_y)

                                hwnd_agarrado, titulo_agarrado = hwnd_bajo, titulo_bajo
                                estado_arrastre = "agarrado"
                            except (pywintypes.error, ValueError):
                                pass  # la ventana desapareció justo al intentar agarrarla, se ignora este frame
                    elif estado_arrastre == "agarrado" and cursor_x is not None:
                        try:
                            mover_ventana(hwnd_agarrado, cursor_x - offset_x, cursor_y - offset_y)
                        except ValueError:
                            # se cerró/crasheó la ventana mientras se arrastraba
                            estado_arrastre = "sin_agarre"
                            hwnd_agarrado, titulo_agarrado = None, None
                    # si cursor_x es None este frame, no hacemos nada: se mantiene el
                    # estado tal cual hasta el próximo frame con posición válida
                else:
                    if estado_arrastre == "agarrado" and hwnd_agarrado is not None and cursor_x is not None:
                        try:
                            aplicar_snap_si_corresponde(hwnd_agarrado, cursor_x, cursor_y)
                        except ValueError:
                            pass  # se cerró justo al soltar, no pasa nada
                    estado_arrastre = "sin_agarre"
                    hwnd_agarrado, titulo_agarrado = None, None

                current_time = time.time()
                fps = 1.0 / (current_time - prev_time) if current_time != prev_time else 0.0
                prev_time = current_time

                cv2.putText(frame, f"FPS: {fps:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                if cursor_x is not None:
                    cv2.putText(
                        frame,
                        f"Cursor: ({int(cursor_x)}, {int(cursor_y)})",
                        (10, 65),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2,
                    )
                    _, titulo_ventana = obtener_ventana_bajo_cursor()
                    if titulo_ventana:
                        cv2.putText(
                            frame,
                            f"Bajo el cursor: {titulo_ventana}",
                            (10, 95),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (255, 255, 0),
                            1,
                        )
                else:
                    cv2.putText(
                        frame,
                        f"Mano derecha no detectada (cursor quieto)",
                        (10, 65),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2,
                    )

                if estado_arrastre == "agarrado":
                    cv2.putText(
                        frame,
                        f"Arrastrando: {titulo_agarrado}",
                        (10, 125),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 140, 255),
                        2,
                    )
                else:
                    cv2.putText(
                        frame,
                        "Arrastre: libre (pellizca sobre una ventana para agarrarla)",
                        (10, 125),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (180, 180, 180),
                        1,
                    )

                cv2.imshow("Fase 5 - Mouse virtual", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
