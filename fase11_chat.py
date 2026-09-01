"""Fase 11 - Viernes con chat de texto además de la voz.

Un solo proceso levanta tres cosas:

  1. El ícono de bandeja (system tray) que ya venía de fase10 — gris /
     azul / naranja según el estado del push-to-talk.
  2. El push-to-talk por voz (mantener F9), reusando
     fase10_asistente_ptt.iniciar_captura_ptt() — nada de esa lógica se
     duplica acá.
  3. Una ventanita de chat abajo a la derecha, estilo ChatGPT: escribís,
     te responde en burbujas, y si el switch "Responder por voz" está
     activo además lo dice en voz alta con fase6_asistente.hablar(). Si lo
     apagás, el chat responde solo por texto (la voz por push-to-talk
     sigue hablando siempre, ese switch no la toca).

La UI usa `customtkinter` (tema oscuro, widgets con bordes redondeados) en
vez de Tkinter pelado. `customtkinter` es Tkinter por debajo, así que
valen las mismas reglas de hilos.

Cancelar ("parar el pensamiento"): mientras se procesa un mensaje, el
botón de enviar se transforma en un botón de stop. No se puede matar el
hilo worker de verdad (Playwright/Whisper son bloqueantes), así que se usa
un contador de "generación": al cancelar se incrementa, y cuando el worker
termina, si su generación quedó vieja el resultado se descarta (no se
muestra ni se habla). El trabajo de fondo sigue hasta terminar solo, pero
es inofensivo.

Detalle de hilos (importante en Windows): pystray y Tkinter se pelean por
el bucle de mensajes del hilo principal. Por eso la bandeja va con
`icon.run_detached()` (su propio hilo) y Tkinter se queda con el
`mainloop()` del hilo principal. Todo lo que viene de otros hilos (worker
del chat, callback de estado del teclado) se encola en `self._cola` y lo
consume el poller de Tk con `after()` — nunca se toca un widget desde
afuera del hilo de Tk.

Comandos que entiende son los mismos de siempre
(fase9_asistente_voz.interpretar_y_ejecutar: abrir apps, volumen, leer
ventana, chatgpt). Si escribís algo que no matchea ningún comando y
USAR_CHATGPT_FALLBACK está activo, la pregunta se manda a ChatGPT para que
el chat sea útil como asistente general, no solo un disparador de
comandos."""

import queue
import sys
import threading
from datetime import datetime

import customtkinter as ctk
import pystray

import fase6_asistente as asistente
import fase10_asistente_ptt as ptt
from fase9_asistente_voz import interpretar_y_ejecutar
from faster_whisper import WhisperModel

TITULO = "Viernes"
ANCHO, ALTO = 420, 600
MARGEN_PANTALLA = 24  # px desde el borde inferior-derecho
USAR_CHATGPT_FALLBACK = True  # comando no reconocido -> se lo pregunta a ChatGPT

# --- paleta oscura (todos los colores en un solo lugar) ---
COL_FONDO = "#16171a"
COL_SUPERFICIE = "#1e2024"
COL_SUPERFICIE_2 = "#2a2d33"
COL_BORDE = "#33363d"
COL_TEXTO = "#e9eaec"
COL_TEXTO_TENUE = "#8b9096"
COL_ACENTO = "#3b82f6"
COL_ACENTO_HOVER = "#2f6fd6"
COL_PELIGRO = "#ef4444"
COL_PELIGRO_HOVER = "#dc2626"
COL_VIERNES = "#22d3ee"  # identidad de "Viernes" (nombre + detalles)
COL_BURBUJA_USUARIO = "#3b6ef5"
COL_BURBUJA_ASIS = "#24262b"

# estado del push-to-talk -> (texto, color del puntito)
_ESTADO_PTT = {
    "escuchando": ("Escuchando… mantené F9", COL_ACENTO),
    "procesando": ("Transcribiendo lo que dijiste…", "#f59e0b"),
    "inactivo": ("A tus órdenes · F9 para hablar", COL_TEXTO_TENUE),
}

_ANIM_PENSANDO = ("●  ∙  ∙", "∙  ●  ∙", "∙  ∙  ●", "∙  ●  ∙")


class VentanaChat:
    def __init__(self, root):
        self.root = root
        self._cola = queue.Queue()  # callables a correr en el hilo de Tk
        self._burbujas = []  # labels a los que reajustar el wraplength al redimensionar
        self._pensando = None  # fila de la burbuja "pensando…" en curso
        self._ancho_chat = ANCHO - 60  # ancho útil del área de chat (se actualiza en <Configure>)
        self._anim_id = None  # id del after() que anima esa burbuja
        self._anim_i = 0
        self._generacion = 0  # se incrementa al enviar y al cancelar (ver docstring)

        root.title(TITULO)
        root.geometry(self._geometria_abajo_derecha())
        root.minsize(360, 420)
        root.configure(fg_color=COL_FONDO)
        # La X termina todo el proceso; minimizar esconde la ventana a la
        # bandeja (se vuelve a abrir con "Mostrar chat").
        root.protocol("WM_DELETE_WINDOW", self.cerrar_todo)
        root.bind("<Unmap>", self._al_minimizar)

        self.responder_voz = ctk.BooleanVar(value=True)
        self.fuente = ctk.CTkFont(family="Segoe UI", size=13)
        self.fuente_chica = ctk.CTkFont(family="Segoe UI", size=11)
        self.fuente_nombre = ctk.CTkFont(family="Segoe UI Semibold", size=11)
        self.fuente_titulo = ctk.CTkFont(family="Segoe UI Semibold", size=15)

        self._construir_cabecera()
        self._construir_chat()
        self._construir_barra_entrada()

        self._agregar(
            "asistente",
            "A tus órdenes. Escribime lo que necesites o mantené F9 para hablarme.\n"
            "Abro apps, manejo el volumen, leo la ventana activa y respondo preguntas.",
        )
        self.entrada.focus_set()
        self.root.after(100, self._procesar_cola)

    # ------------------------------------------------------------------ layout
    def _geometria_abajo_derecha(self):
        self.root.update_idletasks()
        x = self.root.winfo_screenwidth() - ANCHO - MARGEN_PANTALLA
        y = self.root.winfo_screenheight() - ALTO - MARGEN_PANTALLA - 40
        return f"{ANCHO}x{ALTO}+{x}+{y}"

    def _construir_cabecera(self):
        barra = ctk.CTkFrame(self.root, fg_color=COL_SUPERFICIE, corner_radius=0, height=60)
        barra.pack(fill="x")
        barra.pack_propagate(False)

        izq = ctk.CTkFrame(barra, fg_color="transparent")
        izq.pack(side="left", padx=18)
        titulo = ctk.CTkFrame(izq, fg_color="transparent")
        titulo.pack(anchor="w")
        ctk.CTkLabel(titulo, text="⚡", font=self.fuente_titulo, text_color=COL_VIERNES).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(titulo, text="Viernes", font=self.fuente_titulo, text_color=COL_TEXTO).pack(side="left")
        self.estado_lbl = ctk.CTkLabel(
            izq, text=_ESTADO_PTT["inactivo"][0], font=self.fuente_chica, text_color=COL_TEXTO_TENUE
        )
        self.estado_lbl.pack(anchor="w", pady=(1, 0))

        self.punto = ctk.CTkLabel(barra, text="●", font=self.fuente, text_color=COL_TEXTO_TENUE)
        self.punto.pack(side="right", padx=18)

    def _construir_chat(self):
        self.chat = ctk.CTkScrollableFrame(self.root, fg_color=COL_FONDO)
        self.chat.pack(fill="both", expand=True, padx=8, pady=4)
        # El canvas interno es el ancho real disponible para las burbujas
        # (ya sin la scrollbar); la CTkScrollableFrame por fuera la incluye.
        self.chat._parent_canvas.bind("<Configure>", self._al_redimensionar)

    def _construir_barra_entrada(self):
        cont = ctk.CTkFrame(self.root, fg_color=COL_SUPERFICIE, corner_radius=0)
        cont.pack(fill="x")

        fila = ctk.CTkFrame(cont, fg_color="transparent")
        fila.pack(fill="x", padx=14, pady=(14, 8))
        self.entrada = ctk.CTkEntry(
            fila,
            placeholder_text="Escribí un mensaje…",
            font=self.fuente,
            height=42,
            corner_radius=21,
            fg_color=COL_SUPERFICIE_2,
            border_color=COL_BORDE,
            border_width=1,
        )
        self.entrada.pack(side="left", fill="x", expand=True)
        self.entrada.bind("<Return>", lambda _e: self._enviar())
        self.boton = ctk.CTkButton(
            fila,
            text="➤",
            width=46,
            height=42,
            corner_radius=21,
            font=ctk.CTkFont(size=16),
            fg_color=COL_ACENTO,
            hover_color=COL_ACENTO_HOVER,
            command=self._enviar,
        )
        self.boton.pack(side="left", padx=(10, 0))

        self.switch_voz = ctk.CTkSwitch(
            cont,
            text="Responder por voz",
            variable=self.responder_voz,
            font=self.fuente_chica,
            text_color=COL_TEXTO_TENUE,
            progress_color=COL_VIERNES,
            button_color=COL_TEXTO,
        )
        self.switch_voz.pack(anchor="w", padx=18, pady=(0, 14))

    def _modo_boton(self, modo):
        if modo == "parar":
            self.boton.configure(text="■", fg_color=COL_PELIGRO, hover_color=COL_PELIGRO_HOVER, command=self._parar)
        else:
            self.boton.configure(text="➤", fg_color=COL_ACENTO, hover_color=COL_ACENTO_HOVER, command=self._enviar)

    # ------------------------------------------------ puente entre hilos (Tk)
    def encolar(self, funcion):
        self._cola.put(funcion)

    def _procesar_cola(self):
        try:
            while True:
                self._cola.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._procesar_cola)

    # -------------------------------------------- mostrar / ocultar / cerrar
    def mostrar(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.entrada.focus_set()

    def _al_minimizar(self, evento):
        if evento.widget is self.root and self.root.state() == "iconic":
            self.root.withdraw()  # sacar también el botón de la barra de tareas

    def cerrar_todo(self):
        self.root.quit()  # corta el mainloop; main() limpia lo demás

    # ------------------------------------------------ estado del push-to-talk
    def set_estado_ptt(self, nombre):
        texto, color = _ESTADO_PTT.get(nombre, _ESTADO_PTT["inactivo"])
        self.estado_lbl.configure(text=texto)
        self.punto.configure(text_color=color)

    # --------------------------------------------------------------- burbujas
    def _wraplength(self):
        # Tope duro: la burbuja NUNCA debe ser más ancha que el área de
        # chat, o el CTkLabel (que ES la burbuja) desborda su fila y se ve
        # recortado a los lados. Dejo ~60px de aire para paddings.
        # OJO: customtkinter interpreta `wraplength` en px SIN escalar y lo
        # multiplica por el factor de escala del display (150% acá), así que
        # hay que dividir por esa escala el ancho real en px que queremos.
        objetivo_px = max(180, min(int(self._ancho_chat * 0.80), self._ancho_chat - 60))
        return int(objetivo_px / self.chat._get_widget_scaling())

    def _al_redimensionar(self, evento):
        if abs(evento.width - self._ancho_chat) < 2:
            return
        self._ancho_chat = evento.width
        ancho = self._wraplength()
        for lbl in self._burbujas:
            lbl.configure(wraplength=ancho)

    def _agregar(self, quien, texto):
        es_usuario = quien == "usuario"
        lado = "e" if es_usuario else "w"

        fila = ctk.CTkFrame(self.chat, fg_color="transparent")
        fila.pack(fill="x", pady=(8, 2))

        cabecera = "Vos" if es_usuario else "Viernes"
        ctk.CTkLabel(
            fila,
            text=f"{cabecera}  ·  {datetime.now():%H:%M}",
            font=self.fuente_nombre,
            text_color=COL_TEXTO_TENUE if es_usuario else COL_VIERNES,
        ).pack(anchor=lado, padx=10, pady=(0, 3))

        burbuja = ctk.CTkLabel(
            fila,
            text=texto.strip(),
            font=self.fuente,
            justify="left",
            wraplength=self._wraplength(),
            fg_color=COL_BURBUJA_USUARIO if es_usuario else COL_BURBUJA_ASIS,
            text_color="#ffffff" if es_usuario else COL_TEXTO,
            corner_radius=16,
            padx=14,
            pady=10,
        )
        burbuja.pack(anchor=lado, padx=6)
        self._burbujas.append(burbuja)
        self._scroll_al_final()
        return fila

    def _agregar_sistema(self, texto):
        fila = ctk.CTkFrame(self.chat, fg_color="transparent")
        fila.pack(fill="x", pady=6)
        ctk.CTkLabel(fila, text=texto, font=self.fuente_chica, text_color=COL_TEXTO_TENUE).pack()
        self._scroll_al_final()

    def _scroll_al_final(self):
        self.chat.update_idletasks()
        self.chat._parent_canvas.yview_moveto(1.0)

    # ------------------------------------------------------- burbuja "pensando"
    def _iniciar_pensando(self):
        fila = ctk.CTkFrame(self.chat, fg_color="transparent")
        fila.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(
            fila, text="Viernes", font=self.fuente_nombre, text_color=COL_VIERNES
        ).pack(anchor="w", padx=10, pady=(0, 3))
        etiqueta = ctk.CTkLabel(
            fila,
            text=_ANIM_PENSANDO[0],
            font=self.fuente,
            fg_color=COL_BURBUJA_ASIS,
            text_color=COL_TEXTO_TENUE,
            corner_radius=16,
            padx=16,
            pady=10,
        )
        etiqueta.pack(anchor="w", padx=6)
        self._pensando = fila
        self._anim_i = 0
        self._animar_pensando(etiqueta)
        self._scroll_al_final()

    def _animar_pensando(self, etiqueta):
        etiqueta.configure(text=_ANIM_PENSANDO[self._anim_i % len(_ANIM_PENSANDO)])
        self._anim_i += 1
        self._anim_id = self.root.after(350, lambda: self._animar_pensando(etiqueta))

    def _terminar_pensando(self):
        if self._anim_id is not None:
            self.root.after_cancel(self._anim_id)
            self._anim_id = None
        if self._pensando is not None:
            self._pensando.destroy()
            self._pensando = None

    # -------------------------------------------------------------------- chat
    def _enviar(self):
        texto = self.entrada.get().strip()
        if not texto or self.entrada.cget("state") == "disabled":
            return
        self.entrada.delete(0, "end")
        self._agregar("usuario", texto)
        self._generacion += 1
        gen = self._generacion
        self.entrada.configure(state="disabled")
        self._modo_boton("parar")
        self._iniciar_pensando()
        con_voz = self.responder_voz.get()
        threading.Thread(target=self._trabajar, args=(texto, con_voz, gen), daemon=True).start()

    def _parar(self):
        self._generacion += 1  # invalida el resultado que venga del worker en curso
        self._terminar_pensando()
        self._agregar_sistema("— cancelado —")
        self.estado_lbl.configure(text=_ESTADO_PTT["inactivo"][0])
        self._reactivar()

    def _reactivar(self):
        self.entrada.configure(state="normal")
        self._modo_boton("enviar")
        self.entrada.focus_set()

    def _trabajar(self, texto, con_voz, gen):
        """Corre en un hilo aparte — interpretar_y_ejecutar y sobre todo
        preguntar_chatgpt (Playwright) pueden tardar varios segundos."""
        try:
            respuesta = interpretar_y_ejecutar(texto)
            if respuesta is None and USAR_CHATGPT_FALLBACK:
                self.encolar(lambda: self.estado_lbl.configure(text="Le estoy preguntando a ChatGPT…"))
                respuesta = asistente.preguntar_chatgpt(texto)
        except Exception as error:  # que un fallo no deje el chat trabado
            respuesta = f"Uy, algo falló: {error}"

        if respuesta is None:
            respuesta = "No entendí eso, o no es algo que sepa hacer todavía."
        self.encolar(lambda: self._mostrar_respuesta(respuesta, con_voz, gen))

    def _mostrar_respuesta(self, respuesta, con_voz, gen):
        if gen != self._generacion:
            return  # el usuario canceló (o mandó otro mensaje): descarto este resultado
        self._terminar_pensando()
        self.set_estado_ptt("inactivo")
        self._reactivar()
        self._agregar("asistente", respuesta)
        if con_voz:
            threading.Thread(target=asistente.hablar, args=(respuesta,), daemon=True).start()


def main():
    print("=== Fase 11: Viernes con chat + push-to-talk ===")
    print(f"  Cargando modelo '{ptt.MODELO_COMANDO}'…")
    modelo = WhisperModel(ptt.MODELO_COMANDO, device="cpu", compute_type="int8")
    print("  Listo. Ícono en la bandeja: click para abrir el chat, F9 para hablar.")

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    ventana = VentanaChat(root)
    root.withdraw()  # arranca oculta: solo el ícono de bandeja

    def al_estado_ptt(nombre):
        icono.icon = ptt._ICONOS_ESTADO[nombre]
        ventana.encolar(lambda: ventana.set_estado_ptt(nombre))

    limpiar_ptt = ptt.iniciar_captura_ptt(modelo, al_estado_ptt)

    def _mostrar(_icon, _item):
        ventana.encolar(ventana.mostrar)

    def _salir(_icon, _item):
        ventana.encolar(ventana.cerrar_todo)

    icono = pystray.Icon(
        "viernes",
        icon=ptt.ICONO_INACTIVO,
        title="Viernes",
        menu=pystray.Menu(
            pystray.MenuItem("Mostrar chat", _mostrar, default=True),
            pystray.MenuItem("Salir", _salir),
        ),
    )
    icono.run_detached()

    try:
        root.mainloop()
    finally:
        limpiar_ptt()
        icono.stop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("El resto del proyecto asume Windows.", file=sys.stderr)
    main()
