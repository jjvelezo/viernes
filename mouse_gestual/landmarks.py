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


def dibujar_landmarks(frame, hand_landmarks, w, h):
    puntos = [(int(lm.x * w), int(lm.y * h)) for lm in hand_landmarks]

    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, puntos[a], puntos[b], (255, 255, 255), 2)
    for x, y in puntos:
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)


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

                for hand_landmarks, handedness in zip(result.hand_landmarks, result.handedness, strict=True):
                    dibujar_landmarks(frame, hand_landmarks, w, h)

                    label = handedness[0].category_name  # "Left" / "Right"
                    text = LABELS_ES.get(label, label)

                    wrist = hand_landmarks[0]
                    text_x = int(wrist.x * w)
                    text_y = int(wrist.y * h) - 20

                    cv2.putText(
                        frame,
                        text,
                        (text_x, max(text_y, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (255, 0, 0),
                        2,
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

                cv2.imshow("Detección de manos", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
