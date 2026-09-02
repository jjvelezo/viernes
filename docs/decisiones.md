# Decisiones y trampas

Cosas que se probaron y **no** funcionaron, y números calibrados a mano.
Antes de "arreglar" algo que parezca raro en estas áreas, leé esto. El
detalle fino de cada tema vive en el docstring del módulo correspondiente;
acá está el resumen.

---

## Motor LLM

- **`Gemma 4 E2B`** (variante litert-lm, cuantizada) + runtime
  **`litert-lm-api`**, backend **GPU**. Ganó una comparativa inicial contra
  tres alternativas en Ollama (`gemma` 7B q4, `phi4-mini`, `xlam:1b`): 10/10
  en las frases de prueba de tool-calling en español, 1–2 s por comando en
  GPU vs. 5–9 s en CPU. Entra en los 4 GB de VRAM de una GTX 1650.
  - Ollama con un modelo de 7B: 8/10 de precisión pero 10–100 s por comando
    (no entra en 4 GB, corre 78 % en CPU).
  - `phi4-mini`: formato de tool-call inconsistente entre respuestas, a
    veces alucina que ya hizo la acción.
  - `xlam:1b` (especialista en function-calling): <1 s pero 4/10 de
    precisión real, y por diseño **no responde preguntas generales** (su
    system prompt lo obliga a negarse si ninguna función aplica).
  - Con `phi4-mini` y `xlam:1b` hubo que escribir un parser manual del
    JSON de tool-call porque Ollama solo lo hace nativo para familias que
    reconoce. Con litert-lm-api el tool-calling funcionó nativo desde el
    primer intento (Google es dueño del modelo y del runtime).
- **Dato de hardware:** el backend GPU de litert-lm-api usa **WebGPU sobre
  Direct3D 12**, no CUDA. No hace falta el CUDA Toolkit, solo el driver de
  NVIDIA.
- **Gemma no guarda estado entre turnos:** cada `create_conversation` es una
  charla nueva. El contexto conversacional lo aporta
  [`core/memoria.py`](../agente/core/memoria.py), que reconstruye el
  historial (resumen progresivo + ventana de turnos verbatim) y lo pasa como
  `messages=` en cada turno. **No se mantiene vivo el objeto `Conversation`**
  a propósito: chocaría con el `Lock` que serializa los turnos y con que las
  tools cambian por turno (`skills.activas()`). Reconstruir es barato.
- El **resumidor progresivo** es el mismo Gemma 4 E2B (no se baja otro
  modelo): resume con imprecisiones, así que se le pide poco y concreto y
  siempre quedan los últimos turnos verbatim. La compresión corre dentro del
  turno que desborda la ventana → le suma ~1–2 s a ese turno puntual.
- Persistencia de conversaciones: un JSON por charla en `privado/chats/`,
  reescrito entero por turno (son chicas), atómico con `os.replace`.
- `motor_llm` serializa los turnos con un `Lock` — se usa desde el
  push-to-talk y desde el chat. La creación del `Engine` también va bajo ese
  candado.
- El modelo a veces no genera texto después de ejecutar una tool simple
  (queda "mudo"): `ejecutar_turno()` cae al string que devolvió la tool como
  fallback, vía un `ToolEventHandler` que registra nombre/args/resultado de
  cada llamada.

## STT (Whisper)

- `faster-whisper` `small`, `device="cpu"`, `compute_type="int8"`. Backend
  `ctranslate2` (no hay torch). `small` en CPU alcanza; no hizo falta `base`.
- **`initial_prompt` con nombres de apps/marcas**: Whisper mezcla español con
  inglés mal ("chrome" → "chorme"). Al sumar un nombre a `APPS_CONOCIDAS` en
  `skills/apps.py`, agregarlo también a `PROMPT_INICIAL` en `core/voz.py`.
- Audio mono float32 a 16 kHz, sin resampleo (faster-whisper asume esa tasa
  con un array NumPy directo).
- **Bug del F9 sin hablar**: apretar y soltar F9 sin decir nada devolvía como
  transcripción el propio `initial_prompt` (Whisper, sobre silencio, repite
  el prompt). Se mitiga con: puerta de amplitud (pico < 0.01 → ""),
  `vad_filter=True` + `condition_on_previous_text=False`, y un filtro
  explícito (`_es_eco_del_prompt`).

## Wake word — descartado

Un prototipo de wake word ("Viernes" con `SequenceMatcher` ≥ 0.65 sobre la
transcripción — los modelos chicos garabatean: "vierna", "biennes",
"piernas") llegó a funcionar. Se descartó: push-to-talk explícito es más
predecible que una detección always-on.

## TTS (voz)

- **Motor por defecto: Piper** (local, offline, CPU). `tts.motor` en
  `config.json`: `auto` (Piper si hay modelo, si no edge) / `piper` / `edge`.
  El `.onnx` va en `privado/voz_piper/` (no en el repo). Si Piper falla
  (carga o síntesis) cae a `edge-tts`.
- Velocidad de Piper: `tts.piper_length_scale` (menor = más rápido; 0.8 ≈
  15 % más rápido que 1.0; bajar de ~0.7 suena atropellado).
- **Cache de frases cortas** (`privado/tts_cache/`, ≤ 140 chars): saludos,
  confirmaciones y errores se sintetizan una vez y se reproducen de disco.
  La clave incluye motor + voz + velocidad.
- `edge-tts` genera solo el mp3; se reproduce con **MCI de Windows**
  (`winmm.dll`, `type mpegvideo`) porque `sounddevice`/`winsound` no
  decodifican mp3.
- **NO se usa `"play ... wait"`**: ese bloqueo no se corta de forma
  confiable con un `stop` de otro hilo (barge-in). Se lanza el play y se
  sondea el estado cada 50 ms → la voz se calla en ~50 ms, no al final.
- `_generar_mp3_edge` corre en un hilo nuevo: si el turno usó Playwright
  antes, un `asyncio.run()` directo revienta con `RuntimeError`; un hilo
  nuevo arranca con asyncio limpio.
- Voces probadas y descartadas antes de Piper: SAPI/OneCore Sabina, OneCore
  Laura/Helena (España), edge-tts Salomé (Colombia), Dalia (México). Para
  cambiar la voz de edge: `tts.voice` en `config.json`.

## Barge-in

Interrumpir al agente hablando (apretar F9 de nuevo) se resolvió con una
máquina de 3 estados (idle / procesando / hablando):

- durante **procesando** (transcribiendo o pensando el LLM) se ignora F9 —
  no hay audio para cortar y tocar el modelo desde dos lados a la vez es
  justo lo que el `Lock` evita;
- durante **hablando** (reproducción TTS) sí se puede interrumpir, cortando
  el audio en seco con un `stop` de MCI sobre el mismo alias.

## Ventana de chat = pywebview + WebView2 (no customtkinter)

customtkinter se sintió "limitado / barato". Se migró a `pywebview` +
`pythonnet` sobre el WebView2 Runtime que ya trae Windows con Edge (no
Electron, no Node, no Chromium empaquetado). Costo: ~+100 MB RAM (2 procesos
de Edge); no toca el presupuesto de VRAM de Gemma. La UI es HTML/CSS puro en
[`core/chat_ui/index.html`](../agente/core/chat_ui/index.html).

Detalles que costaron (no repetir el error):

- **Blur detrás de la ventana: no se puede.** Acrílico y blur vía DWM
  (`SetWindowCompositionAttribute`) devuelven éxito pero la superficie de
  Chromium/WebView2 los tapa: la ventana queda gris opaca. `transparent=True`
  de pywebview en edgechromium tampoco da alpha real.
  **Lo que sí funciona**: `transparent=False` + fondo negro sólido en el CSS
  + **alpha uniforme de toda la ventana** con
  `SetLayeredWindowAttributes(hwnd, 0, ALPHA, LWA_ALPHA)`. Es alpha plano, no
  "frosted", pero confiable en cualquier Windows.
- **Arrastre**: el drag-region por JS de pywebview va a tirones (mueve la
  ventana con `moveTo` en cada mousemove). El fix es el arrastre **nativo**:
  en `mousedown` de la cabecera, Python hace `ReleaseCapture()` +
  `SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)`.
- **Esquinas redondeadas**: nada de `SetWindowRgn` (deja las esquinas feas y
  come la sombra). Redondeo por CSS + `overflow:hidden`. El recuadro blanco
  de ~2 px que dibuja DWM en la ventana frameless se mata con
  `DwmSetWindowAttribute(hwnd, DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED)`.
- **Pin (siempre encima)**: `SetWindowPos` es un objeto **compartido** en
  todo el proceso — pywebview lo usa para mover la ventana. **No** tocarle
  `.argtypes` (rompe el arrastre con `ArgumentError`); pasar los dos slots
  HWND como `ctypes.c_void_p` para que el `-1` (HWND_TOPMOST) se extienda
  bien a 64 bits.
- **`SetProcessDPIAware()` a mano**: no llamarlo → desincroniza el tamaño del
  frame vs. el WebView2 y deja la ventana medio pintada.
- **Screenshots**: `PIL.ImageGrab` captura negro sobre WebView2 (superficie
  GPU). Para capturas: `PrintWindow(hwnd, dc, 2)` (PW_RENDERFULLCONTENT). Aun
  así el fondo sale gris neutro (sin el escritorio real detrás) → la
  translucidez no se puede juzgar desde un screenshot, hay que correrlo.
- **No** exponer el objeto `webview.Window` como atributo público del
  `js_api` → pywebview intenta serializarlo recursivamente y spamea errores;
  usar atributos privados (`self._window` / `self._hwnd`).
- **Dos temas conmutables** (`moderno` glass / `retro` CRT ámbar): un solo
  `index.html`, todo el retro colgado de `:root[data-tema="retro"]`. pywebview
  le mete `%3F` a un `file://...?query`, así que el tema no se puede pasar por
  URL; y `localStorage` no persiste entre instancias de ventana pywebview en
  `file://`. Solución: `chat_web.crear()` lee el HTML como string, hace
  `.replace('data-tema="moderno"', …)` y lo pasa por `html=` (no `url=`).

## Skill `chatgpt`

Playwright + perfil persistente de Chromium en `privado/perfil_chatgpt/`.
Sin login (chatgpt.com permite mensajes anónimos de una vez).

- `headless=True` **no** funciona: Cloudflare detecta headless y sirve "Just
  a moment...". Ventana fuera de pantalla tampoco (Chromium la trata como
  segundo plano y frena el render en vivo). Funciona `--start-minimized`.
- Selectores (clases CSS con hash, sin `id` estable):
  `get_by_placeholder("Pregúntale a ChatGPT")`,
  `[data-conversation-transcript]`. Si se rompe, re-inspeccionar el DOM vivo.
- `viewport` 1400×900 obligatorio (más angosto = composer "mobile" distinto).
- Se envía clickeando "Enviar mensaje", no con Enter (Enter a veces dejaba
  el mensaje sin enviar). Se espera el fin por el botón "Detener la
  generación", no sondeando texto.
- Cada pregunta lleva una instrucción de "respuesta directa y **siempre una
  oración completa, nunca un número suelto**" — una respuesta numérica corta
  la renderiza chatgpt.com con algo que no es texto del DOM y era
  inextraíble.
- Extracción: se prueban **todos** los `[data-conversation-transcript]`, no
  solo `.first`.

## Skill `spotify`

Web API con `urllib` (transporte en `skills/_spotify_api.py`). Requiere
Premium + `scripts/spotify_auth.py` una vez.

- La reproducción necesita un **dispositivo activo** o falla con 404. Se
  abre la **página específica** del track/playlist en `open.spotify.com`
  (no la home — no registra dispositivo ni tras 16 s). Fast-path: si la API
  ya reporta un dispositivo, no se abre nada.
- Con un `device_id`, se transfiere la reproducción a él de forma explícita
  (`PUT /me/player`): un dispositivo viejo puede figurar "activo" y estar
  mudo, y las órdenes se aceptan sin error y no suena nada.
- `search` con `limit=1` da resultados incorrectos ("bohemian rhapsody" →
  "Billie Jean"). Se pide `limit=3+` y se toma el primero.
- Las playlists del usuario se paginan (puede haber > 50).

## Skill `internet`

Tavily (`/search` con `include_answer`) y **no** scrapear un buscador:
devuelve la respuesta ya redactada, así Gemma no sintetiza. Si falla o no
hay key, cae a la skill `chatgpt`. El contexto del usuario
(`internet.contexto_usuario`: país / moneda / zona) se antepone al query —
sin eso "cuánto vale el dólar" salía en pesos de otro país.

## Regla de aislamiento `agente/` ↔ `mouse_gestual/`

`agente/` no importa una sola línea de `mouse_gestual/`. Lo que se
necesitaba de ahí (llamadas `win32gui` para cerrar ventanas, por ejemplo)
se **reescribió** mínimo en la skill correspondiente. El mouse gestual se
lanza como `subprocess` con el Python del venv. Así `agente/` puede moverse
a su propio repo sin arrastrar nada, y si el mouse gestual se cuelga no se
lleva puesto al asistente.

## Mouse gestual

- Solo coordenadas **x,y** de los landmarks. El eje `z` (profundidad de
  MediaPipe) se descartó por ruidoso — **no reintroducir gestos con `z`**.
- Corrección de lateralidad: la etiqueta Left/Right de MediaPipe está
  invertida respecto del frame en espejo y es poco fiable con las dos manos
  juntas — `main.py` la reemplaza por el orden en `x`.
- Click y arrastre son el mismo gesto crudo (pellizco), se distinguen por
  duración: contador de frames crudos independiente del debounce.
- El cursor real se **congela** mientras la mano que lo controla hace puño.
  Sin esto, scrollear arrastraba el cursor fuera de la ventana.
- Suavizado en dos etapas (media móvil + exponencial); el margen de mapeo
  amplifica el temblor por `1/(1-2·margen)`, por eso existe `DEADZONE_CURSOR`.
- La captura de cámara usa `cv2.CAP_MSMF`: era el cuello de botella real, no
  MediaPipe (que corre en CPU en el build pip de Windows a ~80 fps).

## Varios

- `sd.InputStream` devuelto por una función debe guardarse en una variable
  con lifetime largo (global) — si se descarta, el objeto se destruye
  mientras el audio sigue activo y crashea el proceso con `STATUS_BREAKPOINT`
  (código `-2147483645`), sin traceback Python.
- `abrir_app` usa un índice dinámico de los `.lnk` del Menú Inicio (resueltos
  por `WScript.Shell`), no un diccionario fijo — así solo ofrece apps
  realmente instaladas y se actualiza solo. Mismo patrón (match por
  contención antes que fuzzy total) en `abrir_carpeta` y en la elección de
  playlist de Spotify.
- "Ventana activa" (foco) ≠ "la ventana que el usuario nombró": si nombra una
  app hay que buscarla por título (`EnumWindows`), y ante 0 o varias
  coincidencias no adivinar — devolver la ambigüedad como error para que
  aclare.
