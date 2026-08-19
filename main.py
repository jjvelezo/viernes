"""Fase 5 - Mouse virtual: cualquiera de las dos manos mueve el cursor real
del sistema y arrastra ventanas con un pellizco. Pellizcar con AMBAS manos
a la vez redimensiona la ventana: la derecha hace de esquina superior
derecha, la izquierda de esquina inferior izquierda."""

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
from fase3_gestos import FRAMES_DEBOUNCE, EstadoDebounce, detectar_gesto
from fase4_cursor import (
    MANO_CURSOR,
    aplicar_snap_si_corresponde,
    cerrar_ventana,
    clic_izquierdo,
    esta_maximizada,
    mover_cursor,
    mover_ventana,
    obtener_limites_virtuales,
    obtener_ventana_en_punto,
    redimensionar_ventana,
    restaurar_ventana,
)

INDICE_PUNTA = 8  # landmark de la punta del dedo índice
MANOS = ("Left", "Right")

HISTORIAL_LEN = 4       # cantidad de posiciones a promediar para filtrar temblor
FACTOR_SUAVIZADO = 0.75  # suavizado exponencial sobre la posición filtrada (1.0 = sin suavizado)
# Margen de cada borde de la cámara que no mapea a pantalla: cuanto más alto,
# menos hay que mover la mano para llegar a los bordes/esquinas de la pantalla
# (la zona activa dentro del cuadro se achica). El de Y va más alto porque
# levantar la mano cerca del borde superior del encuadre es lo más incómodo
# y además donde MediaPipe pierde antes la detección (mano saliendo de cuadro).
MARGEN_X = 0.20
MARGEN_Y = 0.30

ALTURA_AGARRE = 20  # distancia (px) del cursor al borde superior de la ventana al agarrarla, simula agarrar la barra de título

MIN_FRAMES_CLICK = 2  # frames mínimos de pellizco crudo para que cuente como click (filtra ruido de 1 frame)
TIEMPO_CIERRE = 2.5    # segundos de puño sostenido (mientras se arrastra) para cerrar la ventana agarrada

# MediaPipe detecta la mano invertida respecto al frame espejado, por eso se corrige acá
VOLTEAR_HANDEDNESS = True


def mapear_con_margen(valor, margen):
    ajustado = (valor - margen) / (1 - 2 * margen)
    return min(max(ajustado, 0.0), 1.0)


def mano_real(label):
    """Corrige la mano que devuelve MediaPipe ("Left"/"Right") por el
    efecto espejo del frame volteado."""
    if VOLTEAR_HANDEDNESS:
        return "Right" if label == "Left" else "Left"
    return label


def main():
    # cv2.VideoCapture(0) sin backend explícito termina usando uno más lento
    # en Windows (~21 FPS reales aunque la cámara pueda dar 30). Forzar MSMF
    # + un buffer de 1 frame evita además que se acumulen frames viejos en
    # cola (esa cola es la que se siente como "el mouse va atrasado" aunque
    # el modelo procese de sobra).
    cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

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

    # Estado de seguimiento por mano: posición suavizada, historial para
    # filtrar temblor, y debounce de gesto — todo indexado "Left"/"Right".
    posiciones = {mano: None for mano in MANOS}
    historiales_x = {mano: deque(maxlen=HISTORIAL_LEN) for mano in MANOS}
    historiales_y = {mano: deque(maxlen=HISTORIAL_LEN) for mano in MANOS}
    estados_gesto = {mano: EstadoDebounce() for mano in MANOS}

    # Arrastre con una mano
    estado_arrastre = "sin_agarre"  # "sin_agarre" | "agarrado"
    hwnd_agarrado, titulo_agarrado = None, None
    mano_arrastre = None
    offset_x, offset_y = 0, 0
    tiempo_inicio_cierre = None  # timestamp de cuándo empezó el puño sostenido, o None

    # Redimensionar con las dos manos a la vez
    estado_resize = "sin_agarre"  # "sin_agarre" | "agarrado"
    hwnd_resize, titulo_resize = None, None

    # Click con pellizco corto: cuenta frames de pellizco crudo (sin debounce)
    # por mano, y si se suelta antes de llegar a convertirse en arrastre, es un click.
    contador_pellizco_crudo = {mano: 0 for mano in MANOS}
    llego_a_agarrar = {mano: False for mano in MANOS}

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
                detectada = {mano: False for mano in MANOS}
                gestos_actuales = {mano: "NINGUNO" for mano in MANOS}

                manos_detectadas = list(zip(result.hand_landmarks, result.handedness))

                # El clasificador Left/Right de MediaPipe se confunde bastante cuando
                # ve las DOS manos a la vez (cerca, cruzadas, en poses simétricas), y
                # eso hacía que el control saltara de mano sin avisar. Con dos manos en
                # cuadro, usamos la posición real en X como fuente de verdad en vez de
                # confiar en la etiqueta: con el frame espejado, la mano más a la
                # derecha en la imagen ES la mano derecha, sin ambigüedad posible.
                if len(manos_detectadas) == 2:
                    manos_detectadas.sort(key=lambda par: par[0][INDICE_PUNTA].x)
                    orden_mano = ["Left", "Right"]
                else:
                    orden_mano = None

                for i, (hand_landmarks, handedness) in enumerate(manos_detectadas):
                    dibujar_landmarks(frame, hand_landmarks, w, h)

                    mano = orden_mano[i] if orden_mano else mano_real(handedness[0].category_name)
                    detectada[mano] = True
                    gestos_actuales[mano] = detectar_gesto(hand_landmarks)

                    punta_indice = hand_landmarks[INDICE_PUNTA]
                    historiales_x[mano].append(punta_indice.x)
                    historiales_y[mano].append(punta_indice.y)
                    x_filtrado = sum(historiales_x[mano]) / len(historiales_x[mano])
                    y_filtrado = sum(historiales_y[mano]) / len(historiales_y[mano])

                    x_ajustado = mapear_con_margen(x_filtrado, MARGEN_X)
                    y_ajustado = mapear_con_margen(y_filtrado, MARGEN_Y)
                    objetivo_x = x_min_pantalla + x_ajustado * ancho_pantalla
                    objetivo_y = y_min_pantalla + y_ajustado * alto_pantalla

                    anterior = posiciones[mano]
                    if anterior is None:
                        posiciones[mano] = (objetivo_x, objetivo_y)
                    else:
                        ax, ay = anterior
                        posiciones[mano] = (
                            ax + (objetivo_x - ax) * FACTOR_SUAVIZADO,
                            ay + (objetivo_y - ay) * FACTOR_SUAVIZADO,
                        )

                    px, py = int(punta_indice.x * w), int(punta_indice.y * h)
                    cv2.circle(frame, (px, py), 10, (0, 255, 255), -1)

                for mano in MANOS:
                    if not detectada[mano]:
                        posiciones[mano] = None
                        historiales_x[mano].clear()
                        historiales_y[mano].clear()

                gesto_confirmado = {mano: estados_gesto[mano].actualizar(gestos_actuales[mano]) for mano in MANOS}
                pellizco_der = gesto_confirmado["Right"] == "PELLIZCO"
                pellizco_izq = gesto_confirmado["Left"] == "PELLIZCO"

                # --- cursor real del sistema ---
                # Redimensionando (ambas manos): no se mueve el cursor real.
                # Arrastrando (una mano): la mano que arrastra controla el cursor.
                # Libre: preferimos la derecha si está, si no la izquierda.
                if pellizco_der and pellizco_izq:
                    mano_cursor = None
                elif pellizco_der and not pellizco_izq:
                    mano_cursor = "Right"
                elif pellizco_izq and not pellizco_der:
                    mano_cursor = "Left"
                elif detectada[MANO_CURSOR]:
                    mano_cursor = MANO_CURSOR
                elif detectada["Left"]:
                    mano_cursor = "Left"
                elif detectada["Right"]:
                    mano_cursor = "Right"
                else:
                    mano_cursor = None

                if mano_cursor is not None and posiciones[mano_cursor] is not None:
                    mover_cursor(*posiciones[mano_cursor])

                # --- arrastre con una sola mano ---
                mano_activa = "Right" if (pellizco_der and not pellizco_izq) else (
                    "Left" if (pellizco_izq and not pellizco_der) else None
                )
                gesto_mano_arrastre = gesto_confirmado.get(mano_arrastre) if mano_arrastre else None

                if estado_arrastre == "agarrado" and gesto_mano_arrastre == "PUÑO":
                    # Cerrar: se cerró el puño sin soltar la ventana. Se sostiene la
                    # posición (no sigue al cursor) mientras corre la cuenta regresiva.
                    if tiempo_inicio_cierre is None:
                        tiempo_inicio_cierre = time.time()
                    elif time.time() - tiempo_inicio_cierre >= TIEMPO_CIERRE:
                        try:
                            cerrar_ventana(hwnd_agarrado)
                        except ValueError:
                            pass
                        estado_arrastre = "sin_agarre"
                        hwnd_agarrado, titulo_agarrado, mano_arrastre = None, None, None
                        tiempo_inicio_cierre = None
                elif mano_activa is not None:
                    tiempo_inicio_cierre = None  # se volvió a pellizcar: cancela cualquier cuenta de cierre
                    pos = posiciones[mano_activa]
                    if estado_arrastre == "sin_agarre" and pos is not None:
                        cx, cy = pos
                        hwnd_bajo, titulo_bajo = obtener_ventana_en_punto(cx, cy)
                        if hwnd_bajo is not None and titulo_bajo:
                            try:
                                if esta_maximizada(hwnd_bajo):
                                    restaurar_ventana(hwnd_bajo)

                                # Como en Windows: no importa dónde caiga el pellizco dentro
                                # de la ventana, se agarra siempre "por la barra de título".
                                izq, arriba, derecha, _ = win32gui.GetWindowRect(hwnd_bajo)
                                ancho_ventana = derecha - izq
                                offset_x = ancho_ventana / 2
                                offset_y = ALTURA_AGARRE
                                mover_ventana(hwnd_bajo, cx - offset_x, cy - offset_y)

                                hwnd_agarrado, titulo_agarrado = hwnd_bajo, titulo_bajo
                                mano_arrastre = mano_activa
                                estado_arrastre = "agarrado"
                                llego_a_agarrar[mano_activa] = True
                            except (pywintypes.error, ValueError):
                                pass  # la ventana desapareció justo al intentar agarrarla
                    elif estado_arrastre == "agarrado" and mano_arrastre == mano_activa and pos is not None:
                        try:
                            mover_ventana(hwnd_agarrado, pos[0] - offset_x, pos[1] - offset_y)
                        except ValueError:
                            estado_arrastre = "sin_agarre"
                            hwnd_agarrado, titulo_agarrado = None, None
                    elif estado_arrastre == "agarrado" and mano_arrastre != mano_activa:
                        # cambió la mano que pellizca sin soltar antes: soltamos sin snap
                        estado_arrastre = "sin_agarre"
                        hwnd_agarrado, titulo_agarrado = None, None
                    # si pos es None este frame (tracking momentáneo perdido), no hacemos
                    # nada: se mantiene el estado tal cual hasta el próximo frame válido
                else:
                    tiempo_inicio_cierre = None
                    if estado_arrastre == "agarrado":
                        pos_suelta = posiciones.get(mano_arrastre)
                        if hwnd_agarrado is not None and pos_suelta is not None:
                            try:
                                aplicar_snap_si_corresponde(hwnd_agarrado, pos_suelta[0], pos_suelta[1])
                            except ValueError:
                                pass
                        estado_arrastre = "sin_agarre"
                        hwnd_agarrado, titulo_agarrado = None, None

                # --- redimensionar con las dos manos ---
                if pellizco_der and pellizco_izq and posiciones["Right"] is not None and posiciones["Left"] is not None:
                    der_x, der_y = posiciones["Right"]
                    izq_x, izq_y = posiciones["Left"]

                    if estado_resize == "sin_agarre":
                        medio_x, medio_y = (der_x + izq_x) / 2, (der_y + izq_y) / 2
                        hwnd_bajo, titulo_bajo = obtener_ventana_en_punto(medio_x, medio_y)
                        if hwnd_bajo is not None and titulo_bajo:
                            try:
                                if esta_maximizada(hwnd_bajo):
                                    restaurar_ventana(hwnd_bajo)
                                hwnd_resize, titulo_resize = hwnd_bajo, titulo_bajo
                                estado_resize = "agarrado"
                                llego_a_agarrar["Right"] = True
                                llego_a_agarrar["Left"] = True
                            except (pywintypes.error, ValueError):
                                pass
                    elif estado_resize == "agarrado":
                        try:
                            # Derecha = esquina superior derecha, izquierda = esquina inferior izquierda
                            redimensionar_ventana(hwnd_resize, izq_x, der_y, der_x, izq_y)
                        except ValueError:
                            estado_resize = "sin_agarre"
                            hwnd_resize, titulo_resize = None, None
                else:
                    estado_resize = "sin_agarre"
                    hwnd_resize, titulo_resize = None, None

                # --- click con pellizco corto ---
                # Cuenta frames de pellizco CRUDO (sin debounce) por mano. Si se suelta
                # antes de llegar a FRAMES_DEBOUNCE (el umbral que usa el arrastre para
                # confirmarse) y esa mano nunca llegó a agarrar nada, fue un toque rápido.
                for mano in MANOS:
                    if gestos_actuales[mano] == "PELLIZCO":
                        contador_pellizco_crudo[mano] += 1
                    else:
                        duracion = contador_pellizco_crudo[mano]
                        if MIN_FRAMES_CLICK <= duracion < FRAMES_DEBOUNCE and not llego_a_agarrar[mano]:
                            clic_izquierdo()
                        contador_pellizco_crudo[mano] = 0
                        llego_a_agarrar[mano] = False

                # --- Overlay de información ---
                current_time = time.time()
                fps = 1.0 / (current_time - prev_time) if current_time != prev_time else 0.0
                prev_time = current_time

                cv2.putText(frame, f"FPS: {fps:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                if mano_cursor is not None and posiciones[mano_cursor] is not None:
                    cx, cy = posiciones[mano_cursor]
                    cv2.putText(
                        frame,
                        f"Cursor ({mano_cursor}): ({int(cx)}, {int(cy)})",
                        (10, 65),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2,
                    )
                else:
                    cv2.putText(
                        frame,
                        "Sin mano detectada (cursor quieto)",
                        (10, 65),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2,
                    )

                if tiempo_inicio_cierre is not None:
                    restante = max(TIEMPO_CIERRE - (time.time() - tiempo_inicio_cierre), 0)
                    cv2.putText(
                        frame,
                        f"Cerrando '{titulo_agarrado}' en {restante:.1f}s (abri la mano para cancelar)",
                        (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 0, 255),
                        2,
                    )
                elif estado_resize == "agarrado":
                    cv2.putText(
                        frame,
                        f"Redimensionando: {titulo_resize}",
                        (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 0, 255),
                        2,
                    )
                elif estado_arrastre == "agarrado":
                    cv2.putText(
                        frame,
                        f"Arrastrando ({mano_arrastre}): {titulo_agarrado}",
                        (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 140, 255),
                        2,
                    )
                else:
                    cv2.putText(
                        frame,
                        "Libre: pellizca una ventana (1 mano = mover, 2 manos = redimensionar)",
                        (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
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
