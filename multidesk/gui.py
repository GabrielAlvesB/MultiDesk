"""Janela do MultiDesk (customtkinter) + ícone na bandeja do sistema."""

import logging
import os
import queue
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from . import __version__, autostart
from .config import load_config, save_config
from .link import DEFAULT_PORT, local_ip, make_link, new_code
from .tray import Tray

ICON = Path(__file__).resolve().parent / "assets" / "icon.png"
# Só em 127.0.0.1: garante uma única janela aberta (a variável é para testes).
INSTANCE_PORT = int(os.environ.get("MULTIDESK_INSTANCE_PORT", 24851))

SIDE_LABEL = {"left": "esquerda", "right": "direita", "top": "acima", "bottom": "abaixo"}
CLOSE_LABEL = {"ask": "Perguntar", "tray": "Ficar na bandeja", "quit": "Encerrar"}
_OLD_SIDES = {v: k for k, v in SIDE_LABEL.items()}  # configs antigas guardavam em português

# Paleta (claro, escuro)
ACCENT = ("#2563EB", "#3B82F6")
ACCENT_HOVER = ("#1D4ED8", "#2563EB")
DANGER = ("#DC2626", "#EF4444")
DANGER_HOVER = ("#B91C1C", "#DC2626")
CARD = ("#FFFFFF", "#1F2937")
BG = ("#F1F5F9", "#111827")
MUTED = ("#64748B", "#94A3B8")
TEXT = ("#0F172A", "#F1F5F9")
BORDER = ("#E2E8F0", "#374151")
OK, WARN, BAD, IDLE = "#16A34A", "#D97706", "#DC2626", "#94A3B8"


def ping_color(ms):
    if ms is None:
        return IDLE
    return OK if ms < 30 else WARN if ms < 100 else BAD


def fmt_ping(ms):
    if ms is None:
        return "— ms"
    return "<1 ms" if ms < 1 else f"{ms:.0f} ms"


def _mode_color(pair):
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.q.put(self.format(record))


class LayoutMap(ctk.CTkFrame):
    """Desenho dos monitores: o do meio e até 4 vizinhos (esquerda/direita/acima/abaixo)."""

    POS = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}

    def __init__(self, master, on_pick=None, height=250, empty_blank=False):
        super().__init__(master, fg_color="transparent")
        self.on_pick = on_pick
        self.empty_blank = empty_blank
        self.canvas = tk.Canvas(self, height=height, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._hover)
        self.center = {"title": "", "sub": "", "style": "main"}
        self.slots = {}  # lado -> dict(title, sub, sub_color, style)
        self._hit = {}

    def set(self, center, slots):
        self.center, self.slots = center, slots
        self.redraw()

    def _scale(self):
        return self.canvas.winfo_fpixels("1i") / 96

    def _rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        bg = _mode_color(CARD)
        c.configure(bg=bg)
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        s = self._scale()
        mh = min(h / 3.6, (w / 3.9) / 1.6)
        mw = mh * 1.6
        gx, gy = mw * 0.18, mh * 0.2
        cx, cy = w / 2, h / 2
        self._hit = {}
        self._monitor(cx, cy, mw, mh, s, **self.center)
        for side, (dx, dy) in self.POS.items():
            x, y = cx + dx * (mw + gx), cy + dy * (mh + gy)
            slot = self.slots.get(side)
            if slot:
                self._monitor(x, y, mw, mh, s, **slot)
            else:
                self._empty(x, y, mw, mh, s)
            self._hit[side] = (x - mw / 2, y - mh / 2, x + mw / 2, y + mh / 2)

    def _monitor(self, x, y, mw, mh, s, title="", sub="", sub_color=None, style="main"):
        c = self.canvas
        fills = {"main": ACCENT, "peer": ("#DBEAFE", "#1E3A5F"), "active": ACCENT, "server": ("#E2E8F0", "#374151")}
        fill = _mode_color(fills.get(style, ACCENT))
        light_text = style in ("main", "active")
        fg = "#FFFFFF" if light_text else _mode_color(TEXT)
        x0, y0, x1, y1 = x - mw / 2, y - mh / 2, x + mw / 2, y + mh / 2
        if style == "active":  # contorno de "controlando agora"
            self._rect(x0 - 4 * s, y0 - 4 * s, x1 + 4 * s, y1 + 4 * s, 12 * s, fill=OK, outline="")
        self._rect(x0, y0, x1, y1, 9 * s, fill=fill, outline="")
        c.create_text(x, y - (7 * s if sub else 0), text=title, fill=fg,
                      font=("Segoe UI", int(-12 * s), "bold"), width=mw - 10 * s)
        if sub:
            col = "#FFFFFF" if light_text else (sub_color or _mode_color(MUTED))
            c.create_text(x, y + 11 * s, text=sub, fill=col, font=("Segoe UI", int(-11 * s), "bold"))

    def _empty(self, x, y, mw, mh, s):
        c = self.canvas
        col = _mode_color(BORDER)
        c.create_rectangle(x - mw / 2, y - mh / 2, x + mw / 2, y + mh / 2, outline=col, dash=(4, 3), width=max(1, int(1.5 * s)))
        hint = "escolher" if self.on_pick else "" if self.empty_blank else "livre"
        c.create_text(x, y, text=hint, fill=_mode_color(MUTED), font=("Segoe UI", int(-11 * s)))

    def _side_at(self, ev):
        for side, (x0, y0, x1, y1) in self._hit.items():
            if x0 <= ev.x <= x1 and y0 <= ev.y <= y1:
                return side
        return None

    def _click(self, ev):
        side = self._side_at(ev)
        if side and self.on_pick:
            self.on_pick(side)

    def _hover(self, ev):
        self.canvas.configure(cursor="hand2" if self.on_pick and self._side_at(ev) else "")


class ModeToggle(ctk.CTkFrame):
    """Seletor de modo em formato de pílula com dois botões."""

    def __init__(self, master, options, command):
        super().__init__(master, fg_color=("#E2E8F0", "#1F2937"), corner_radius=12)
        self.command = command
        self.buttons = {}
        for i, (key, label) in enumerate(options):
            b = ctk.CTkButton(self, text=label, height=36, corner_radius=9,
                              font=ctk.CTkFont(size=13, weight="bold"),
                              command=lambda k=key: self.command(k))
            b.grid(row=0, column=i, sticky="ew", padx=4, pady=4)
            self.grid_columnconfigure(i, weight=1, uniform="m")
            self.buttons[key] = b
        self.value = options[0][0]

    def set(self, key):
        self.value = key
        for k, b in self.buttons.items():
            if k == key:
                b.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF")
            else:
                b.configure(fg_color="transparent", hover_color=BORDER, text_color=MUTED)

    def configure_state(self, state):
        for b in self.buttons.values():
            b.configure(state=state)


class App:
    def __init__(self, root, autostarted=False):
        self.root = root
        self.cfg = load_config()
        self.worker = None  # Server ou Client em execução
        self.ui_q = queue.Queue()  # ações vindas de outras threads
        self.log_q = queue.Queue()
        logging.getLogger().addHandler(QueueHandler(self.log_q))

        ctk.set_appearance_mode("system")
        root.title("MultiDesk")
        root.geometry("640x700")
        root.minsize(560, 620)
        root.configure(fg_color=BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            self._icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass

        self.mode = self.cfg.get("mode", "server")
        side = _OLD_SIDES.get(self.cfg.get("side"), self.cfg.get("side"))
        self.side = side if side in SIDE_LABEL else "right"
        self.code = self.cfg["code"]

        self.tray = Tray(ICON, self.post, self.tray_status, lambda: self.worker is not None,
                         {"show": self.show, "toggle": self.toggle, "quit": self.quit})
        self._build()
        self.show_mode()
        self.refresh()
        root.after(100, self.poll)
        root.after(1000, self.tick)
        if autostarted:
            # Aberto no login: já começa no modo salvo, discreto. Só some de vez
            # se houver ícone na bandeja; senão fica minimizado na barra de tarefas.
            root.iconify()
            root.after(500, self.toggle)
            root.after(2000, lambda: self.tray.available and self.root.withdraw())

    # ---- montagem da janela ---------------------------------------------

    def _card(self, parent, title=None):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        if title:
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=TEXT).pack(anchor="w", padx=16, pady=(12, 4))
        return card

    def _build(self):
        root = self.root
        f_title = ctk.CTkFont(size=20, weight="bold")
        f_small = ctk.CTkFont(size=12)

        # Cabeçalho
        head = ctk.CTkFrame(root, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 8))
        try:
            from PIL import Image
            logo = ctk.CTkImage(Image.open(ICON), size=(36, 36))
            ctk.CTkLabel(head, image=logo, text="").pack(side="left")
        except Exception:
            pass
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left", padx=10)
        ctk.CTkLabel(titles, text="MultiDesk", font=f_title, text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text=f"Um mouse e um teclado para vários PCs  ·  v{__version__}", font=f_small,
                     text_color=MUTED).pack(anchor="w")
        self.pill = ctk.CTkLabel(head, text="  Parado  ", corner_radius=12, fg_color=IDLE,
                                 text_color="#FFFFFF", font=ctk.CTkFont(size=12, weight="bold"), height=26)
        self.pill.pack(side="right")

        # Escolha do modo
        self.mode_btn = ModeToggle(root, [("server", "Compartilhar este PC"), ("client", "Ser controlado")],
                                   self._mode_changed)
        self.mode_btn.set(self.mode)
        self.mode_btn.pack(fill="x", padx=20, pady=(4, 10))

        self.body = ctk.CTkFrame(root, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20)
        self._build_server(self.body)
        self._build_client(self.body)

        # Rodapé
        foot = self.foot = ctk.CTkFrame(root, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(10, 14))
        opts = ctk.CTkFrame(foot, fg_color="transparent")
        opts.pack(side="left")
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(anchor="w")
        self.auto_var = tk.BooleanVar(value=autostart.is_enabled())
        ctk.CTkSwitch(row, text="Iniciar com o sistema", variable=self.auto_var, progress_color=ACCENT,
                      command=self.toggle_autostart, font=f_small).pack(side="left")
        self.log_btn = ctk.CTkButton(row, text="Ver log", width=80, height=28, fg_color="transparent",
                                     border_width=1, border_color=BORDER, text_color=TEXT,
                                     hover_color=BORDER, command=self.toggle_log)
        self.log_btn.pack(side="left", padx=10)
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(row, text="Ao fechar a janela:", font=f_small, text_color=MUTED).pack(side="left")
        self.close_var = tk.StringVar(value=CLOSE_LABEL[self.cfg.get("on_close", "ask")])
        ctk.CTkOptionMenu(row, values=list(CLOSE_LABEL.values()), variable=self.close_var, width=150, height=26,
                          font=f_small, fg_color=BORDER, button_color=BORDER, button_hover_color=MUTED,
                          text_color=TEXT, command=self._close_pref).pack(side="left", padx=(6, 0))
        self.start_btn = ctk.CTkButton(foot, text="Iniciar", width=150, height=40, corner_radius=10,
                                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                       font=ctk.CTkFont(size=15, weight="bold"), command=self.toggle)
        self.start_btn.pack(side="right")

        self.log = ctk.CTkTextbox(root, height=130, font=ctk.CTkFont(family="Consolas", size=11),
                                  fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=10)

    def _build_server(self, parent):
        self.srv = ctk.CTkFrame(parent, fg_color="transparent")

        card = self._card(self.srv, "Link para conectar os outros PCs")
        card.pack(fill="x")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(2, 6))
        self.link_var = tk.StringVar()
        ctk.CTkEntry(row, textvariable=self.link_var, state="readonly", height=36,
                     font=ctk.CTkFont(family="Consolas", size=13), border_color=BORDER).pack(
            side="left", fill="x", expand=True)
        self.copy_btn = ctk.CTkButton(row, text="Copiar", width=90, height=36, fg_color=ACCENT,
                                      hover_color=ACCENT_HOVER, command=self.copy_link)
        self.copy_btn.pack(side="left", padx=(8, 0))
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(opts, text="Porta", text_color=MUTED, font=ctk.CTkFont(size=12)).pack(side="left")
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", DEFAULT_PORT)))
        self.port_entry = ctk.CTkEntry(opts, textvariable=self.port_var, width=70, height=28, border_color=BORDER)
        self.port_entry.pack(side="left", padx=(6, 12))
        self.port_var.trace_add("write", lambda *_: self.update_link())
        self.regen_btn = ctk.CTkButton(opts, text="Gerar novo código", width=130, height=28, fg_color="transparent",
                                       text_color=ACCENT, hover_color=BORDER, command=self.regen_code)
        self.regen_btn.pack(side="left")
        self.update_link()

        card = self._card(self.srv, "Seus computadores")
        card.pack(fill="both", expand=True, pady=(10, 0))
        self.srv_map = LayoutMap(card)
        self.srv_map.pack(fill="both", expand=True, padx=12)
        self.srv_hint = ctk.CTkLabel(card, text="", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.srv_hint.pack(pady=(0, 10))

    def _build_client(self, parent):
        self.cli = ctk.CTkFrame(parent, fg_color="transparent")

        card = self._card(self.cli, "Conectar a um servidor")
        card.pack(fill="x")
        self.join_entry = ctk.CTkEntry(card, height=36, border_color=BORDER,
                                       placeholder_text="Cole aqui o link: multidesk://...",
                                       font=ctk.CTkFont(family="Consolas", size=13))
        self.join_entry.pack(fill="x", padx=16, pady=(2, 8))
        if self.cfg.get("link"):
            self.join_entry.insert(0, self.cfg["link"])
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(row, text="Nome deste PC", text_color=MUTED, font=ctk.CTkFont(size=12)).pack(side="left")
        self.name_var = tk.StringVar(value=self.cfg.get("name", socket.gethostname()))
        self.name_entry = ctk.CTkEntry(row, textvariable=self.name_var, height=28, border_color=BORDER)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        stat = self._card(self.cli)
        stat.pack(fill="x", pady=(10, 0))
        inner = ctk.CTkFrame(stat, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)
        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.cli_status = ctk.CTkLabel(left, text="Parado", font=ctk.CTkFont(size=15, weight="bold"),
                                       text_color=TEXT, anchor="w")
        self.cli_status.pack(anchor="w")
        self.cli_detail = ctk.CTkLabel(left, text="Clique em Iniciar para conectar", text_color=MUTED,
                                       font=ctk.CTkFont(size=12), anchor="w")
        self.cli_detail.pack(anchor="w")
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right")
        self.cli_ping = ctk.CTkLabel(right, text="— ms", font=ctk.CTkFont(size=26, weight="bold"), text_color=IDLE)
        self.cli_ping.pack(anchor="e")
        ctk.CTkLabel(right, text="ping", text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="e")

        card = self._card(self.cli, "Onde esta tela fica em relação ao servidor?")
        card.pack(fill="both", expand=True, pady=(10, 0))
        self.cli_map = LayoutMap(card, on_pick=self.pick_side, height=210, empty_blank=True)
        self.cli_map.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    # ---- ações ------------------------------------------------------------

    def _mode_changed(self, value):
        if self.worker:  # não troca de modo com algo rodando
            return
        self.mode = value
        self.mode_btn.set(value)
        self.show_mode()
        self.refresh()

    def show_mode(self):
        self.srv.pack_forget()
        self.cli.pack_forget()
        (self.srv if self.mode == "server" else self.cli).pack(fill="both", expand=True)

    def pick_side(self, side):
        if self.worker:
            return
        self.side = side
        self.refresh()

    def regen_code(self):
        if self.worker:
            messagebox.showinfo("MultiDesk", "Pare o servidor antes de trocar o código.")
            return
        if self.cfg.get("link_shared") and not messagebox.askyesno(
                "MultiDesk", "Os PCs que já usam o link atual vão precisar do link novo. Continuar?"):
            return
        self.code = new_code()
        self.update_link()

    def update_link(self):
        self.link_var.set(make_link(local_ip(), self.port_var.get().strip(), self.code))

    def copy_link(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.link_var.get())
        self.cfg["link_shared"] = True
        self.copy_btn.configure(text="Copiado!", fg_color=OK)
        self.root.after(1500, lambda: self.copy_btn.configure(text="Copiar", fg_color=ACCENT))

    def toggle_log(self):
        # A janela cresce/encolhe junto para o log não espremer o resto.
        scale = self.root.winfo_fpixels("1i") / 96
        w = round(self.root.winfo_width() / scale)
        h = round(self.root.winfo_height() / scale)
        if self.log.winfo_ismapped():
            self.log.pack_forget()
            self.log_btn.configure(text="Ver log")
            self.root.geometry(f"{w}x{max(h - 150, 620)}")
        else:
            self.log.pack(fill="x", padx=20, pady=(0, 4), after=self.foot)
            self.log_btn.configure(text="Ocultar log")
            self.root.geometry(f"{w}x{h + 150}")

    def toggle_autostart(self):
        try:
            autostart.set_enabled(self.auto_var.get())
        except OSError as e:
            self.auto_var.set(autostart.is_enabled())
            messagebox.showerror("MultiDesk", f"Não consegui alterar a inicialização automática:\n{e}")

    def toggle(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        else:
            try:
                self.worker = self.start_server() if self.mode == "server" else self.start_client()
            except Exception as e:
                self.worker = None
                logging.getLogger("multidesk").exception("falha ao iniciar")
                self.show()
                messagebox.showerror("MultiDesk", f"Não consegui iniciar:\n{e}")
                return
            self.save()
        self.refresh()

    def start_server(self):
        from .server import Server
        server = Server(port=int(self.port_var.get()), code=self.code, on_change=self.request_refresh)
        server.start()
        return server

    def start_client(self):
        from .client import Client
        client = Client(self.join_entry.get().strip(), name=self.name_var.get().strip() or None,
                        side=self.side, on_change=self.request_refresh)
        client.start()
        return client

    # ---- atualização da tela ----------------------------------------------

    def post(self, fn):
        """Executa fn na thread da janela (seguro a partir de qualquer thread)."""
        self.ui_q.put(fn)

    def request_refresh(self):
        self.ui_q.put(self.refresh)

    def _state(self):
        """(texto do status, cor) para o selo do cabeçalho e a bandeja."""
        from .server import Server
        w = self.worker
        if w is None:
            return "Parado", IDLE
        if isinstance(w, Server):
            n = len(w.remotes)
            if w.active:
                return f"Controlando {w.active.name}", OK
            return ("Aguardando conexões", ACCENT[0]) if n == 0 else (f"{n} PC{'s' if n > 1 else ''} conectado{'s' if n > 1 else ''}", OK)
        return {"conectado": ("Conectado", OK), "conectando": ("Conectando…", WARN),
                "reconectando": ("Reconectando…", WARN), "código errado": ("Código errado", BAD),
                "recusado": ("Recusado", BAD)}.get(w.status, (w.status.capitalize(), IDLE))

    def tray_status(self):
        text, _ = self._state()
        rtt = getattr(self.worker, "rtt", None)
        return f"{text} · {fmt_ping(rtt)}" if rtt is not None else text

    def refresh(self):
        from .server import Server
        w = self.worker
        running = w is not None
        text, color = self._state()
        self.pill.configure(text=f"  {text}  ", fg_color=color)
        self.start_btn.configure(text="Parar" if running else "Iniciar",
                                 fg_color=DANGER if running else ACCENT,
                                 hover_color=DANGER_HOVER if running else ACCENT_HOVER)
        state = "disabled" if running else "normal"
        self.mode_btn.configure_state(state)
        for widget in (self.port_entry, self.regen_btn, self.join_entry, self.name_entry):
            widget.configure(state=state)
        self.cli_map.on_pick = None if running else self.pick_side

        if self.mode == "server":
            slots = {}
            if isinstance(w, Server):
                for side, r in list(w.remotes.items()):
                    slots[side] = {"title": r.name, "sub": fmt_ping(r.rtt), "sub_color": ping_color(r.rtt),
                                   "style": "active" if w.active is r else "peer"}
            self.srv_map.set({"title": "Este PC", "sub": "servidor", "style": "main"}, slots)
            if not running:
                hint = "Clique em Iniciar e use o link nos outros PCs."
            elif not slots:
                hint = "Aguardando… abra o MultiDesk no outro PC e cole o link."
            else:
                hint = "Leve o mouse até a borda da tela onde o PC está. Scroll Lock prende o cursor."
            self.srv_hint.configure(text=hint)
        else:
            connected = running and w.connected
            rtt = w.rtt if running else None
            self.cli_map.set({"title": (w.server_name if connected and w.server_name else "Servidor"),
                              "sub": "", "style": "server"},
                             {self.side: {"title": "Este PC", "sub": SIDE_LABEL[self.side], "style": "main"}})
            self.cli_status.configure(text=text)
            if connected:
                detail = f"Servidor: {w.server_name} ({w.host})"
            elif running:
                detail = f"Tentando {w.host}:{w.port}…"
            else:
                detail = "Cole o link, escolha a posição e clique em Iniciar"
            self.cli_detail.configure(text=detail)
            self.cli_ping.configure(text=fmt_ping(rtt), text_color=ping_color(rtt))

        self.tray.update(f"MultiDesk — {self.tray_status()}")

    def tick(self):
        # Atualiza o ping mesmo sem eventos e acompanha a troca de tema claro/escuro.
        mode = ctk.get_appearance_mode()
        if mode != getattr(self, "_last_mode", mode):
            self.srv_map.redraw()
            self.cli_map.redraw()
        self._last_mode = mode
        self.refresh()
        self.root.after(1000, self.tick)

    def poll(self):
        pending = set()
        while not self.ui_q.empty():
            fn = self.ui_q.get_nowait()
            if fn == self.refresh:  # junta vários pedidos de atualização em um
                pending.add(fn)
            else:
                fn()
        for fn in pending:
            fn()
        lines = []
        while not self.log_q.empty():
            lines.append(self.log_q.get_nowait())
        if lines:
            self.log.insert("end", "\n".join(lines) + "\n")
            self.log.see("end")
        self.root.after(100, self.poll)

    # ---- janela / ciclo de vida ------------------------------------------

    def save(self):
        try:
            port = int(self.port_var.get())
        except ValueError:
            port = DEFAULT_PORT
        self.cfg.update(mode=self.mode, code=self.code, port=port, link=self.join_entry.get().strip(),
                        name=self.name_var.get(), side=self.side)
        save_config(self.cfg)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _close_pref(self, label):
        self.cfg["on_close"] = next(k for k, v in CLOSE_LABEL.items() if v == label)
        save_config(self.cfg)

    def on_close(self):
        """Fechar a janela: parado encerra; ativo segue a preferência (perguntar/bandeja/encerrar).

        Nunca fica rodando escondido sem um ícone visível na bandeja: senão o
        mouse continuaria indo para o outro PC com o app "fechado".
        """
        if not self.worker:
            self.quit()
            return
        pref = self.cfg.get("on_close", "ask")
        if pref == "quit":
            self.quit()
        elif pref == "tray" and self.tray.available:
            self.hide_to_tray()
        else:
            self._ask_close()

    def hide_to_tray(self):
        self.save()
        self.root.withdraw()
        self.tray.notify("O MultiDesk continua ativo aqui na bandeja. Use Sair no menu para encerrar.")

    def _ask_close(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Fechar o MultiDesk")
        dlg.resizable(False, False)
        dlg.configure(fg_color=BG)
        dlg.transient(self.root)
        who = "compartilhando o mouse e o teclado" if self.mode == "server" else "conectado ao servidor"
        ctk.CTkLabel(dlg, text="O MultiDesk ainda está ativo", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(18, 4))
        tray_ok = self.tray.available
        msg = (f"Ele está {who}. Você pode deixá-lo rodando na bandeja do sistema\n"
               "ou encerrar de vez." if tray_ok else
               f"Ele está {who}. Fechar a janela vai encerrar o MultiDesk.")
        ctk.CTkLabel(dlg, text=msg, justify="left", text_color=MUTED,
                     font=ctk.CTkFont(size=13)).pack(anchor="w", padx=22)
        remember = tk.BooleanVar(value=False)
        if tray_ok:
            ctk.CTkCheckBox(dlg, text="Lembrar minha escolha", variable=remember, font=ctk.CTkFont(size=12),
                            fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(anchor="w", padx=22, pady=(12, 0))
        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(16, 18))

        def choose(action):
            if remember.get():
                self.cfg["on_close"] = action
                self.close_var.set(CLOSE_LABEL[action])
                save_config(self.cfg)
            dlg.destroy()
            self.hide_to_tray() if action == "tray" else self.quit()

        ctk.CTkButton(btns, text="Encerrar MultiDesk", fg_color=DANGER, hover_color=DANGER_HOVER, height=36,
                      command=lambda: choose("quit")).pack(side="right")
        if tray_ok:
            ctk.CTkButton(btns, text="Deixar na bandeja", fg_color=ACCENT, hover_color=ACCENT_HOVER, height=36,
                          command=lambda: choose("tray")).pack(side="right", padx=(0, 8))
        ctk.CTkButton(btns, text="Cancelar", fg_color="transparent", text_color=TEXT, hover_color=BORDER,
                      border_width=1, border_color=BORDER, width=90, height=36,
                      command=dlg.destroy).pack(side="left")
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.after(50, lambda: (dlg.lift(), dlg.focus_force(), dlg.grab_set()))
        self._close_dialog = dlg

    def quit(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.save()
        self.tray.stop()
        self.root.destroy()


def _claim_single_instance():
    """Retorna o socket se esta é a primeira instância; senão pede para a outra aparecer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", INSTANCE_PORT))
        sock.listen()
        return sock
    except OSError:
        sock.close()
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
            c.sendall(b"show")
    except OSError:
        pass
    return None


def main(autostarted=False):
    sock = _claim_single_instance()
    if sock is None:
        return
    root = ctk.CTk()
    app = App(root, autostarted=autostarted)

    def listen():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                if conn.recv(16) == b"show":
                    app.post(app.show)

    threading.Thread(target=listen, name="instance", daemon=True).start()
    root.mainloop()
    sock.close()
