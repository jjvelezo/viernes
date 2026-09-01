"""Clasificación de gestos de mano (PUÑO, PALMA_ABIERTA, PELLIZCO) a
partir de los landmarks, con debounce.

Las funciones de clasificación (`detectar_gesto`, `dedo_extendido`, …) y
`EstadoDebounce` son lógica pura sin I/O — `main.py` las importa como
módulo. El `main()` de este archivo es solo una demo standalone del paso
(cámara + overlay), no lo usa nada más."""

import math
import os
import sys
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

MODEL_PATH = os.path.join(os.path.dirname(__file__), "modelos", "hand_landmarker.task")

LABELS_ES = {"Left": "Mano izquierda", "Right": "Mano derecha"}

# Conexiones entre landmarks para dibujar el esqueleto de la mano
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # pulgar
    (0, 5), (5, 6), (6, 7), (7, 8),          # índice
    (5, 9), (9, 10), (10, 11), (11, 12),     # medio
    (9, 13), (13, 14), (14, 15), (15, 16),   # anular
    (13, 17), (17, 18), (18, 19), (19, 20),  # meñique
    (0, 17),                                  # base de la palma
]

FRAMES_DEBOUNCE = 8    # frames consecutivos necesarios para confirmar un gesto
UMBRAL_PELLIZCO = 0.35  # distancia pulgar-índice / tamaño de mano, por debajo = pellizco

# Índices de MediaPipe: (punta, articulación media) de cada dedo, excepto el pulgar
DEDOS_TIP_PIP = {
    "index": (8, 6),
    "middle": (12, 10),
    "ring": (16, 14),
    "pinky": (20, 18),
}


def dibujar_landmarks(frame, hand_landmarks, w, h):
    puntos = [(int(lm.x * w), int(lm.y * h)) for lm in hand_landmarks]

    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, puntos[a], puntos[b], (255, 255, 255), 2)
    for x, y in puntos:
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)


def distancia(p1, p2):
    return math.hypot(p1.x - p2.x, p1.y - p2.y)


def dedo_extendido(landmarks, tip_idx, pip_idx):
    return landmarks[tip_idx].y < landmarks[pip_idx].y


def pulgar_extendido(landmarks):
    punta = landmarks[4]
    base_pulgar = landmarks[2]
    base_menique = landmarks[17]
    return distancia(punta, base_menique) > distancia(base_pulgar, base_menique)


def dedos_extendidos(landmarks):
    estado = {nombre: dedo_extendido(landmarks, tip, pip) for nombre, (tip, pip) in DEDOS_TIP_PIP.items()}
    estado["thumb"] = pulgar_extendido(landmarks)
    return estado


def detectar_gesto(landmarks):
    escala = distancia(landmarks[0], landmarks[9])  # muñeca -> base dedo medio
    if escala == 0:
        return "NINGUNO"

    dist_pellizco = distancia(landmarks[4], landmarks[8]) / escala
    if dist_pellizco < UMBRAL_PELLIZCO:
        return "PELLIZCO"

    dedos = dedos_extendidos(landmarks)
    if all(dedos.values()):
        return "PALMA_ABIERTA"
    if not any(dedos.values()):
        return "PUÑO"

    return "NINGUNO"


class EstadoDebounce:
    def __init__(self):
        self.gesto_candidato = "NINGUNO"
        self.contador = 0
        self.gesto_confirmado = "NINGUNO"

    def actualizar(self, gesto_actual, n_frames=FRAMES_DEBOUNCE):
        if gesto_actual == self.gesto_candidato:
            self.contador += 1
        else:
            self.gesto_candidato = gesto_actual
            self.contador = 1

        if self.contador >= n_frames:
            self.gesto_confirmado = gesto_actual

        return self.gesto_confirmado


def main():
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: no se pudo abrir la cámara.", file=sys.stderr)
        sys.exit(1)

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.7,
        min_hand_presence_confidence=0.7,
        min_tracking_confidence=0.7,
    )

    prev_time = time.time()

    estados_debounce = {
        "Left": EstadoDebounce(),
        "Right": EstadoDebounce(),
    }

    with vision.HandLandmarker.create_from_options(options) as landmarker:
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print("Error: no se pudo leer el frame de la cámara.", file=sys.stderr)
                    break

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                timestamp_ms = int(time.time() * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)

                h, w, _ = frame.shape

                for hand_landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    dibujar_landmarks(frame, hand_landmarks, w, h)

                    label = handedness[0].category_name  # "Left" / "Right"
                    gesto_actual = detectar_gesto(hand_landmarks)
                    gesto_confirmado = estados_debounce[label].actualizar(gesto_actual)

                    wrist = hand_landmarks[0]
                    text_x = int(wrist.x * w)
                    text_y = int(wrist.y * h) - 20

                    mano_texto = LABELS_ES.get(label, label)
                    cv2.putText(
                        frame,
                        mano_texto,
                        (text_x, max(text_y, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 0, 0),
                        2,
                    )
                    cv2.putText(
                        frame,
                        gesto_confirmado,
                        (text_x, max(text_y, 20) + 35),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1.1,
                        (0, 140, 255),
                        3,
                    )

                current_time = time.time()
                fps = 1.0 / (current_time - prev_time) if current_time != prev_time else 0.0
                prev_time = current_time

                cv2.putText(
                    frame,
                    f"FPS: {fps:.2f}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                )

                cv2.imshow("Gestos", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
