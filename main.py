"""Fase 5 - Mouse virtual: cualquiera de las dos manos mueve el cursor real
del sistema. Pellizco corto = click; pellizco sostenido = arrastrar ventana;
pellizco con AMBAS manos = redimensionar (derecha = esquina superior
derecha, izquierda = esquina inferior izquierda); puño sostenido mientras
se arrastra = cerrar la ventana."""

import math
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
    hacer_scroll,
    lanzar_ventana_lateral,
    mover_cursor,
    mover_ventana,
    obtener_limites_virtuales,
    obtener_ventana_en_punto,
    redimensionar_ventana,
    restaurar_ventana,
)

INDICE_PUNTA = 8  # landmark de la punta del dedo índice
MANOS = ("Left", "Right")

HISTORIAL_LEN = 3       # cantidad de posiciones a promediar para filtrar temblor
FACTOR_SUAVIZADO = 0.8  # suavizado exponencial sobre la posición filtrada (1.0 = sin suavizado)
# Margen de cada borde de la cámara que no mapea a pantalla: cuanto más alto,
# menos hay que mover la mano para llegar a los bordes/esquinas de la pantalla
# (la zona activa dentro del cuadro se achica). El de Y va más alto porque
# levantar la mano cerca del borde superior del encuadre es lo más incómodo
# y además donde MediaPipe pierde antes la detección (mano saliendo de cuadro).
# OJO: agrandar el margen también amplifica el temblor natural de la mano
# (una divide por (1 - 2*margen) en mapear_con_margen), por eso hace falta
# DEADZONE_CURSOR de abajo para compensarlo en precisión de click.
MARGEN_X = 0.20
MARGEN_Y = 0.30

# Si el objetivo nuevo está a menos de esto (en píxeles de pantalla) de donde
# ya está el cursor, no se mueve — filtra el temblor fino que hace difícil
# clickear en cosas chicas, sin agregarle demora al movimiento real (que
# siempre supera este umbral de inmediato).
DEADZONE_CURSOR = 5

# Filtro para la posición del cursor sobre la pantalla (px). True = filtro 1€
# (Casiez et al. 2012): corte adaptativo a la velocidad de la mano, así que
# filtra fuerte con la mano quieta (sin temblor) y casi no filtra en
# movimientos rápidos (sin el "arrastre"/lag que deja un suavizado de factor
# fijo). False = vuelve al suavizado exponencial de FACTOR_SUAVIZADO de
# siempre (código legado, queda comentado más abajo en el bucle principal).
USAR_FILTRO_ONE_EURO = True
ONE_EURO_MINCUTOFF = 1.0  # Hz. Más bajo = más agresivo contra el temblor en reposo, a costa de más lag al arrancar a mover
ONE_EURO_BETA = 0.007     # más alto = reacciona más rápido a movimientos veloces (menos arrastre), pero puede reintroducir algo de temblor
ONE_EURO_DCUTOFF = 1.0    # Hz, suaviza la propia estimación interna de velocidad que usa el filtro

ALTURA_AGARRE = 20  # distancia (px) del cursor al borde superior de la ventana al agarrarla, simula agarrar la barra de título

TIEMPO_CIERRE = 1.5    # segundos de puño sostenido (mientras se arrastra) para cerrar la ventana agarrada

# Gesto de "lanzar" ventana: si al soltar el pellizco la mano venía con
# velocidad horizontal alta, la ventana se manda a la mitad izquierda o
# derecha de la pantalla (como un snap forzado), en vez del snap normal por
# cercanía al borde. HISTORIAL_VELOCIDAD_LEN frames recientes de posición
# (mientras se arrastra) se usan para estimar esa velocidad al soltar.
UMBRAL_VELOCIDAD_LANZAR = 1500  # px/s de velocidad horizontal para que cuente como "lanzazo" (ajustar probando)
HISTORIAL_VELOCIDAD_LEN = 5

MIN_FRAMES_CLICK = 2  # frames mínimos de pellizco crudo para que cuente como click (filtra ruido de 1 frame)

# Scroll con puño cerrado: mientras el puño está cerrado y no se está
# arrastrando nada, mover la mano en Y scrollea. Al abrir la mano, sigue
# "deslizando" con desaceleración en vez de frenar en seco (como el scroll
# con inercia del trackpad). Si el sentido queda invertido, cambiá el
# signo de ESCALA_SCROLL.
ESCALA_SCROLL = 12.0       # convierte px de movimiento de mano en unidades de rueda
FRICCION_SCROLL = 0.90     # cuánta velocidad conserva cada frame tras soltar (más cerca de 1 = desliza más)
UMBRAL_PARAR_SCROLL = 0.3  # px/frame por debajo del cual se considera detenido y para la inercia

# MediaPipe detecta la mano invertida respecto al frame espejado, por eso se corrige acá
VOLTEAR_HANDEDNESS = True


class FiltroOneEuro:
    """Filtro 1€ (Casiez, Roussel y Vogel, 2012): paso-bajo con frecuencia de
    corte adaptativa a la velocidad de la señal. Con la mano quieta filtra
    fuerte (mincutoff bajo -> elimina temblor); al acelerar, el corte sube
    con `beta * |velocidad|` y deja pasar casi toda la señal (evita el lag
    que deja un suavizado de factor fijo en movimientos rápidos)."""

    def __init__(self, mincutoff, beta, dcutoff):
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def filtrar(self, x, t):
        """x: valor crudo de la señal. t: timestamp en segundos."""
        if self.t_prev is None:
            self.x_prev, self.t_prev = x, t
            return x

        dt = max(t - self.t_prev, 1e-6)  # evita división por cero si dos frames comparten timestamp

        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.dcutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev

        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev

        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


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
    filtros_x = {mano: FiltroOneEuro(ONE_EURO_MINCUTOFF, ONE_EURO_BETA, ONE_EURO_DCUTOFF) for mano in MANOS}
    filtros_y = {mano: FiltroOneEuro(ONE_EURO_MINCUTOFF, ONE_EURO_BETA, ONE_EURO_DCUTOFF) for mano in MANOS}
    estados_gesto = {mano: EstadoDebounce() for mano in MANOS}

    # Arrastre con una mano
    estado_arrastre = "sin_agarre"  # "sin_agarre" | "agarrado"
    hwnd_agarrado, titulo_agarrado = None, None
    mano_arrastre = None
    offset_x, offset_y = 0, 0
    tiempo_inicio_cierre = None  # timestamp de cuándo empezó el puño sostenido, o None
    historial_arrastre = deque(maxlen=HISTORIAL_VELOCIDAD_LEN)  # (t, x, y) recientes para el gesto de "lanzar" al soltar

    # Redimensionar con las dos manos a la vez
    estado_resize = "sin_agarre"  # "sin_agarre" | "agarrado"
    hwnd_resize, titulo_resize = None, None

    # Click con pellizco corto: cuenta frames de pellizco crudo (sin debounce)
    # por mano, y si se suelta antes de llegar a convertirse en arrastre, es un click.
    contador_pellizco_crudo = {mano: 0 for mano in MANOS}
    llego_a_agarrar = {mano: False for mano in MANOS}

    # Scroll con puño + inercia
    posicion_y_anterior_puño = {mano: None for mano in MANOS}
    velocidad_scroll = 0.0

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
                t_actual = timestamp_ms / 1000.0  # en segundos, para el filtro 1€ y la velocidad de arrastre
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
                        filtros_x[mano].filtrar(objetivo_x, t_actual)
                        filtros_y[mano].filtrar(objetivo_y, t_actual)
                    elif USAR_FILTRO_ONE_EURO:
                        x_suave = filtros_x[mano].filtrar(objetivo_x, t_actual)
                        y_suave = filtros_y[mano].filtrar(objetivo_y, t_actual)
                        ax, ay = anterior
                        distancia = ((x_suave - ax) ** 2 + (y_suave - ay) ** 2) ** 0.5
                        if distancia > DEADZONE_CURSOR:
                            posiciones[mano] = (x_suave, y_suave)
                        # si no, el objetivo está dentro de la deadzone: se
                        # mantiene la posición actual tal cual (no es "no
                        # actualizar el suavizado", es directamente ignorar
                        # este frame para el cursor). El filtro en sí sigue
                        # actualizando su estado interno igual arriba.
                    else:
                        # --- suavizado exponencial (versión anterior a 1€) ---
                        ax, ay = anterior
                        distancia = ((objetivo_x - ax) ** 2 + (objetivo_y - ay) ** 2) ** 0.5
                        if distancia > DEADZONE_CURSOR:
                            posiciones[mano] = (
                                ax + (objetivo_x - ax) * FACTOR_SUAVIZADO,
                                ay + (objetivo_y - ay) * FACTOR_SUAVIZADO,
                            )
                        # si no, el objetivo está dentro de la deadzone: se
                        # mantiene la posición actual tal cual (no es "no
                        # actualizar el suavizado", es directamente ignorar
                        # este frame para el cursor)

                    px, py = int(punta_indice.x * w), int(punta_indice.y * h)
                    cv2.circle(frame, (px, py), 10, (0, 255, 255), -1)

                for mano in MANOS:
                    if not detectada[mano]:
                        posiciones[mano] = None
                        historiales_x[mano].clear()
                        historiales_y[mano].clear()
                        filtros_x[mano].reset()
                        filtros_y[mano].reset()

                gesto_confirmado = {mano: estados_gesto[mano].actualizar(gestos_actuales[mano]) for mano in MANOS}
                pellizco_der = gesto_confirmado["Right"] == "PELLIZCO"
                pellizco_izq = gesto_confirmado["Left"] == "PELLIZCO"

                # --- cursor real del sistema ---
                # Redimensionando (ambas manos): no se mueve el cursor real.
                # Arrastrando (una mano): la mano que arrastra controla el cursor.
                # Puño (scrolleando): tampoco se mueve el cursor, para que la
                # rueda siga cayendo sobre la misma ventana/control todo el tiempo
                # en vez de arrastrar el mouse fuera de ahí.
                # Libre: preferimos la derecha si está, si no la izquierda.
                if pellizco_der and pellizco_izq:
                    mano_cursor = None
                elif pellizco_der and not pellizco_izq:
                    mano_cursor = "Right"
                elif pellizco_izq and not pellizco_der:
                    mano_cursor = "Left"
                elif detectada[MANO_CURSOR] and gesto_confirmado[MANO_CURSOR] != "PUÑO":
                    mano_cursor = MANO_CURSOR
                elif detectada["Left"] and gesto_confirmado["Left"] != "PUÑO":
                    mano_cursor = "Left"
                elif detectada["Right"] and gesto_confirmado["Right"] != "PUÑO":
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
                                historial_arrastre.clear()
                            except (pywintypes.error, ValueError):
                                pass  # la ventana desapareció justo al intentar agarrarla
                    elif estado_arrastre == "agarrado" and mano_arrastre == mano_activa and pos is not None:
                        try:
                            mover_ventana(hwnd_agarrado, pos[0] - offset_x, pos[1] - offset_y)
                            historial_arrastre.append((t_actual, pos[0], pos[1]))
                        except ValueError:
                            estado_arrastre = "sin_agarre"
                            hwnd_agarrado, titulo_agarrado = None, None
                    elif estado_arrastre == "agarrado" and mano_arrastre != mano_activa:
                        # cambió la mano que pellizca sin soltar antes: soltamos sin snap
                        estado_arrastre = "sin_agarre"
                        hwnd_agarrado, titulo_agarrado = None, None
                        historial_arrastre.clear()
                    # si pos es None este frame (tracking momentáneo perdido), no hacemos
                    # nada: se mantiene el estado tal cual hasta el próximo frame válido
                else:
                    tiempo_inicio_cierre = None
                    if estado_arrastre == "agarrado":
                        pos_suelta = posiciones.get(mano_arrastre)
                        if hwnd_agarrado is not None and pos_suelta is not None:
                            # Velocidad horizontal de soltada, estimada entre el
                            # primer y el último punto guardados durante el
                            # arrastre: si viene rápido, se "lanza" la ventana al
                            # lado en vez de aplicar el snap normal por cercanía.
                            velocidad_x = 0.0
                            if len(historial_arrastre) >= 2:
                                t0, x0, _ = historial_arrastre[0]
                                t1, x1, _ = historial_arrastre[-1]
                                dt = t1 - t0
                                if dt > 0:
                                    velocidad_x = (x1 - x0) / dt

                            try:
                                if abs(velocidad_x) > UMBRAL_VELOCIDAD_LANZAR:
                                    lado = "izquierda" if velocidad_x < 0 else "derecha"
                                    lanzar_ventana_lateral(hwnd_agarrado, lado, pos_suelta[0], pos_suelta[1])
                                else:
                                    aplicar_snap_si_corresponde(hwnd_agarrado, pos_suelta[0], pos_suelta[1])
                            except ValueError:
                                pass
                        estado_arrastre = "sin_agarre"
                        hwnd_agarrado, titulo_agarrado = None, None
                        historial_arrastre.clear()

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

                # --- scroll con puño cerrado (+ inercia al soltar) ---
                mano_puño_libre = None
                if estado_arrastre == "sin_agarre":
                    puño_der = gesto_confirmado["Right"] == "PUÑO"
                    puño_izq = gesto_confirmado["Left"] == "PUÑO"
                    if puño_der and not puño_izq:
                        mano_puño_libre = "Right"
                    elif puño_izq and not puño_der:
                        mano_puño_libre = "Left"

                if mano_puño_libre is not None and posiciones[mano_puño_libre] is not None:
                    _, y_actual = posiciones[mano_puño_libre]
                    y_anterior = posicion_y_anterior_puño[mano_puño_libre]
                    if y_anterior is not None:
                        velocidad_scroll = y_actual - y_anterior
                    posicion_y_anterior_puño[mano_puño_libre] = y_actual
                    # limpiar el registro de la otra mano para que no arranque con
                    # un salto raro si se usa para scrollear más adelante
                    otra_mano = "Left" if mano_puño_libre == "Right" else "Right"
                    posicion_y_anterior_puño[otra_mano] = None
                else:
                    # no hay puño activo controlando: se suelta el seguimiento de
                    # posición, pero la velocidad sigue viva y decae de a poco
                    posicion_y_anterior_puño["Left"] = None
                    posicion_y_anterior_puño["Right"] = None
                    velocidad_scroll *= FRICCION_SCROLL
                    if abs(velocidad_scroll) < UMBRAL_PARAR_SCROLL:
                        velocidad_scroll = 0.0

                if abs(velocidad_scroll) > UMBRAL_PARAR_SCROLL:
                    hacer_scroll(-velocidad_scroll * ESCALA_SCROLL)

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
                elif abs(velocidad_scroll) > UMBRAL_PARAR_SCROLL:
                    cv2.putText(
                        frame,
                        f"Scroll ({'activo' if mano_puño_libre else 'inercia'})",
                        (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 200, 0),
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
