"""Fase 5 - Mouse virtual: la punta del índice de la mano derecha mueve el cursor real del sistema."""

import sys
import time
from collections import deque

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

from fase2_landmarks import MODEL_PATH, HAND_CONNECTIONS, dibujar_landmarks
from fase4_cursor import (
    MANO_CURSOR,
    mover_cursor,
    obtener_limites_virtuales,
    obtener_ventana_bajo_cursor,
)

INDICE_PUNTA = 8  # landmark de la punta del dedo índice

HISTORIAL_LEN = 5       # cantidad de posiciones a promediar para filtrar temblor
FACTOR_SUAVIZADO = 0.6  # suavizado exponencial sobre la posición filtrada (1.0 = sin suavizado)
MARGEN_X = 0.15         # margen de cada borde de la cámara que no mapea a pantalla
MARGEN_Y = 0.15

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

                for hand_landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    dibujar_landmarks(frame, hand_landmarks, w, h)

                    label = handedness[0].category_name
                    if not es_mano_derecha(label):
                        continue

                    mano_derecha_detectada = True
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

                cv2.imshow("Fase 5 - Mouse virtual", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
