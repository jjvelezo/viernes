# mouse_gestual/ — mouse por gestos de la mano

Una webcam + MediaPipe HandLandmarker siguen ambas manos; reglas
geométricas simples sobre x,y (dedo extendido / distancia de pellizco)
mueven el cursor real de Windows, arrastran y redimensionan ventanas,
hacen scroll y cierran apps vía `pywin32`.

Fue la **primera capacidad** que se construyó y de ahí nació el proyecto,
pero hoy es **una skill más de Viernes**: corre como proceso aparte y el
asistente lo prende/apaga por voz (skill `manos_libres` en
[`../agente/skills/manos_libres.py`](../agente/skills/manos_libres.py)).
Nada de `agente/` lo importa — si esto se cuelga, no arrastra al asistente.

## Correr

```
../venv/Scripts/python.exe mouse_gestual/main.py     # el mouse gestual completo
```

Cerrar: `q` con la ventana de OpenCV en foco.

## Piezas (cada una corre sola, como demo del paso)

| Archivo | Qué hace |
|---|---|
| `camara.py`   | Captura de webcam + overlay de FPS |
| `landmarks.py` | + detección/dibujo de los 21 landmarks por mano (módulo: `MODEL_PATH`, `dibujar_landmarks`) |
| `gestos.py`    | + clasificación de gestos (PUÑO / PALMA_ABIERTA / PELLIZCO) con debounce (módulo: `detectar_gesto`, `EstadoDebounce`) |
| `cursor.py`    | Primitivas de cursor/ventana vía `win32api`/`win32gui`, con menú por teclado para probar sin cámara (módulo: `mover_cursor`, `mover_ventana`, …) |
| `main.py`      | Máquina de estados por frame que ata todo lo anterior |
| `modelos/hand_landmarker.task` | Modelo de MediaPipe (se resuelve relativo a esta carpeta) |

`main.py` importa a `landmarks`, `gestos` y `cursor` como módulos — si
cambiás sus funciones/constantes públicas, actualizá `main.py`.
