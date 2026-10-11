"""Janela simples em tkinter para usar o MultiDesk sem linha de comando."""

import logging
import queue
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import autostart
from .config import load_config, save_config
from .link import DEFAULT_PORT, local_ip, make_link, new_code

ICON = Path(__file__).resolve().parent / "assets" / "icon.png"
INSTANCE_PORT = 24851  # só em 127.0.0.1: garante uma única janela aberta

SIDES = {"direita": "right", "esquerda": "left", "acima": "top", "abaixo": "bottom"}


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.q.put(self.format(record))


class App:
    def __init__(self, root, autostarted=False):
        self.root = root
        self.cfg = load_config()
        self.worker = None  # Server ou Client em execução
        self.log_q = queue.Queue()
        logging.getLogger().addHandler(QueueHandler(self.log_q))

        root.title("MultiDesk")
        try:
            self._icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        root.minsize(560, 460)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        pad = {"padx": 10, "pady": 6}

        self.mode = tk.StringVar(value=self.cfg.get("mode", "server"))
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        ttk.Radiobutton(top, text="Este PC compartilha o mouse/teclado (servidor)",
                        variable=self.mode, value="server", command=self.show_mode).pack(anchor="w")
        ttk.Radiobutton(top, text="Este PC é controlado por outro (cliente)",
                        variable=self.mode, value="client", command=self.show_mode).pack(anchor="w")

        # --- servidor
        self.srv = ttk.LabelFrame(root, text="Servidor")
        self.code_var = tk.StringVar(value=self.cfg["code"])
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", DEFAULT_PORT)))
        self.link_var = tk.StringVar()
        row = ttk.Frame(self.srv)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Porta:").pack(side="left")
        ttk.Entry(row, textvariable=self.port_var, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(row, text="Código:").pack(side="left")
        ttk.Entry(row, textvariable=self.code_var, width=18, state="readonly").pack(side="left", padx=4)
        ttk.Button(row, text="Novo código", command=self.regen_code).pack(side="left")
        row = ttk.Frame(self.srv)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Link:").pack(side="left")
        ttk.Entry(row, textvariable=self.link_var, state="readonly").pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Copiar", command=self.copy_link).pack(side="left")
        self.clients_var = tk.StringVar(value="Nenhum cliente conectado")
        ttk.Label(self.srv, textvariable=self.clients_var, foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        # --- cliente
        self.cli = ttk.LabelFrame(root, text="Cliente")
        self.join_var = tk.StringVar(value=self.cfg.get("link", ""))
        self.name_var = tk.StringVar(value=self.cfg.get("name", socket.gethostname()))
        self.side_var = tk.StringVar(value=self.cfg.get("side", "direita"))
        row = ttk.Frame(self.cli)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Link do servidor:").pack(side="left")
        ttk.Entry(row, textvariable=self.join_var).pack(side="left", fill="x", expand=True, padx=4)
        row = ttk.Frame(self.cli)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Nome deste PC:").pack(side="left")
        ttk.Entry(row, textvariable=self.name_var, width=20).pack(side="left", padx=(4, 16))
        ttk.Label(row, text="Fica à/acima/abaixo do servidor:").pack(side="left")
        ttk.Combobox(row, textvariable=self.side_var, values=list(SIDES), width=9,
                     state="readonly").pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="desconectado")
        ttk.Label(self.cli, textvariable=self.status_var, foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        # --- botão + log
        self.bottom = ttk.Frame(root)
        bar = ttk.Frame(self.bottom)
        bar.pack(fill="x", pady=(0, 6))
        self.auto_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(bar, text="Iniciar com o sistema e conectar sozinho", variable=self.auto_var,
                        command=self.toggle_autostart).pack(side="left")
        ttk.Button(bar, text="Sair", command=self.quit).pack(side="right")
        self.start_btn = ttk.Button(bar, text="Iniciar", command=self.toggle)
        self.start_btn.pack(side="right", padx=6)
        self.log = tk.Text(self.bottom, height=12, state="disabled", wrap="word",
                           font=("Consolas" if root.tk.call("tk", "windowingsystem") == "win32" else "monospace", 9))
        self.log.pack(fill="both", expand=True)

        self.update_link()
        self.port_var.trace_add("write", lambda *_: self.update_link())
        self.show_mode()
        self.root.after(100, self.poll)
        if autostarted:
            # Aberto no login: fica escondido e já começa no modo salvo.
            self.root.withdraw()
            self.root.after(500, self.toggle)

    # ----------------------------------------------------------------

    def show_mode(self):
        self.srv.pack_forget()
        self.cli.pack_forget()
        self.bottom.pack_forget()
        (self.srv if self.mode.get() == "server" else self.cli).pack(fill="x", padx=10, pady=6)
        self.bottom.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def regen_code(self):
        if self.worker:
            messagebox.showinfo("MultiDesk", "Pare o servidor antes de trocar o código.")
            return
        self.code_var.set(new_code())
        self.update_link()

    def update_link(self):
        self.link_var.set(make_link(local_ip(), self.port_var.get().strip(), self.code_var.get()))

    def copy_link(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.link_var.get())

    def toggle(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
            self.start_btn.config(text="Iniciar")
            self.clients_var.set("Nenhum cliente conectado")
            self.status_var.set("desconectado")
            return
        try:
            self.worker = self.start_server() if self.mode.get() == "server" else self.start_client()
        except Exception as e:
            self.worker = None
            logging.getLogger("multidesk").exception("falha ao iniciar")
            messagebox.showerror("MultiDesk", f"Não consegui iniciar:\n{e}")
            return
        self.start_btn.config(text="Parar")
        self.save()

    def start_server(self):
        from .server import Server
        server = Server(port=int(self.port_var.get()), code=self.code_var.get(),
                        on_change=lambda: self.root.after(0, self.refresh))
        server.start()
        return server

    def start_client(self):
        from .client import Client
        client = Client(self.join_var.get(), name=self.name_var.get().strip() or None,
                        side=SIDES.get(self.side_var.get()),
                        on_change=lambda: self.root.after(0, self.refresh))
        client.start()
        return client

    def refresh(self):
        from .server import SIDE_PT, Server
        w = self.worker
        if isinstance(w, Server):
            if w.remotes:
                parts = [f"{r.name} ({SIDE_PT[s]})" + (" ◀ controlando" if w.active is r else "")
                         for s, r in w.remotes.items()]
                self.clients_var.set("Conectados: " + ", ".join(parts))
            else:
                self.clients_var.set("Nenhum cliente conectado")
        elif w is not None:
            self.status_var.set(w.status)

    def save(self):
        self.cfg.update(mode=self.mode.get(), code=self.code_var.get(), port=int(self.port_var.get()),
                        link=self.join_var.get(), name=self.name_var.get(), side=self.side_var.get())
        save_config(self.cfg)

    def poll(self):
        lines = []
        while not self.log_q.empty():
            lines.append(self.log_q.get_nowait())
        if lines:
            self.log.config(state="normal")
            self.log.insert("end", "\n".join(lines) + "\n")
            self.log.see("end")
            self.log.config(state="disabled")
        self.root.after(150, self.poll)

    def toggle_autostart(self):
        try:
            autostart.set_enabled(self.auto_var.get())
        except OSError as e:
            self.auto_var.set(autostart.is_enabled())
            messagebox.showerror("MultiDesk", f"Não consegui alterar a inicialização automática:\n{e}")

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        """Fechar a janela com o MultiDesk ativo só esconde; "Sair" encerra."""
        if not self.worker:
            self.quit()
            return
        if not self.cfg.get("hide_tip_shown"):
            self.cfg["hide_tip_shown"] = True
            messagebox.showinfo("MultiDesk", "O MultiDesk continua rodando em segundo plano.\n\n"
                                "Para ver esta janela de novo, abra o MultiDesk pelo menu/atalho. "
                                "Para encerrar de vez, use o botão Sair.")
        self.save()
        self.root.withdraw()

    def quit(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.save()
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
    root = tk.Tk()
    app = App(root, autostarted=autostarted)

    def listen():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                if conn.recv(16) == b"show":
                    root.after(0, app.show)

    threading.Thread(target=listen, name="instance", daemon=True).start()
    root.mainloop()
    sock.close()
