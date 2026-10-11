"""Cliente: o computador controlado pelo mouse/teclado do servidor."""

import logging
import socket
import threading

from . import platform
from .clipboard import ClipboardSync
from .crypto import AuthError, handshake
from .link import parse_link

log = logging.getLogger("multidesk.client")

TIMEOUT = 10.0
PING_INTERVAL = 3.0
RETRY_DELAY = 3.0


class Client:
    def __init__(self, link, name=None, side=None, clipboard=True, on_change=None):
        self.host, self.port, self.code = parse_link(link)
        self.name = name or socket.gethostname()
        self.side = side
        self.on_change = on_change or (lambda: None)
        self.connected = False
        self.status = "desconectado"
        self.injector = platform.Injector()
        self.clipboard = ClipboardSync(self._clip_local) if clipboard else None
        self._chan = None
        self._stop = threading.Event()

    def start(self):
        if self.clipboard:
            self.clipboard.start()
        threading.Thread(target=self.run, name="client", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._chan:
            self._chan.close()
        if self.clipboard:
            self.clipboard.stop()

    def run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except AuthError:
                self._set_status("código errado")
                log.error("O servidor recusou o código de pareamento. Confira o link.")
                return
            except FatalError as e:
                self._set_status("recusado")
                log.error("Servidor recusou: %s", e)
                return
            except (OSError, ConnectionError, ValueError) as e:
                if not self._stop.is_set():
                    log.warning("Conexão com %s:%d falhou: %s", self.host, self.port, e)
            finally:
                self.connected = False
                self.injector.release_all()
            if self._stop.wait(RETRY_DELAY):
                break
            self._set_status("reconectando")
        self._set_status("desconectado")

    def _set_status(self, status):
        self.status = status
        self.on_change()

    def _session(self):
        self._set_status("conectando")
        log.info("Conectando a %s:%d...", self.host, self.port)
        sock = socket.create_connection((self.host, self.port), timeout=TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(TIMEOUT)
        chan = handshake(sock, self.code, is_server=False)
        self._chan = chan
        try:
            chan.send({"t": "hello", "name": self.name, "bounds": list(platform.screen_bounds()),
                       "side": self.side, "os": platform.OS})
            reply = chan.recv()
            if reply.get("t") == "error":
                raise FatalError(reply.get("msg"))
            self.connected = True
            side = {"right": "à direita", "left": "à esquerda", "top": "acima",
                    "bottom": "abaixo"}.get(reply.get("side"), reply.get("side"))
            log.info("Conectado ao servidor %s (esta tela fica %s dele)", reply.get("server"), side)
            self._set_status("conectado")
            pinger = threading.Event()
            threading.Thread(target=self._ping_loop, args=(chan, pinger), daemon=True).start()
            try:
                while not self._stop.is_set():
                    self._handle(chan.recv())
            finally:
                pinger.set()
        finally:
            chan.close()
            self._chan = None

    def _ping_loop(self, chan, done):
        while not done.wait(PING_INTERVAL):
            try:
                chan.send({"t": "ping"})
            except OSError:
                return

    def _handle(self, msg):
        t = msg.get("t")
        inj = self.injector
        try:
            if t in ("move", "enter"):
                inj.move(msg["x"], msg["y"])
            elif t == "key":
                inj.key(msg["key"], msg["down"])
            elif t == "button":
                inj.button(msg["b"], msg["down"])
            elif t == "scroll":
                inj.scroll(msg["dx"], msg["dy"])
            elif t == "leave":
                inj.release_all()
            elif t == "clip" and self.clipboard:
                self.clipboard.set(msg.get("text"))
        except Exception:
            log.exception("erro executando %s", t)

    def _clip_local(self, text):
        chan = self._chan
        if chan and self.connected:
            try:
                chan.send({"t": "clip", "text": text})
            except OSError:
                pass


class FatalError(Exception):
    pass
