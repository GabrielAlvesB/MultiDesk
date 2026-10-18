"""Cliente: o computador controlado pelo mouse/teclado do servidor."""

import logging
import socket
import statistics
import threading
import time
from collections import deque

from . import platform, wifi
from .clipboard import ClipboardSync, clip_message, parse_clip
from .crypto import AuthError, handshake
from .link import parse_link

log = logging.getLogger("multidesk.client")

TIMEOUT = 10.0
PING_INTERVAL = 0.5
PING_SAMPLES = 5  # o ping mostrado é a mediana das últimas medições
NO_PONG_WARN = 5.0
RETRY_DELAY = 3.0


class Client:
    def __init__(self, link, name=None, side=None, clipboard=True, on_change=None):
        self.host, self.port, self.code = parse_link(link)
        self.name = name or socket.gethostname()
        self.side = side
        self.on_change = on_change or (lambda: None)
        self.connected = False
        self.status = "desconectado"
        self.server_name = None
        self.rtt = None  # ping em ms (ida e volta)
        self._rtts = deque(maxlen=PING_SAMPLES)
        self._last_pong = 0.0
        self._wifi = None
        self.injector = platform.Injector()
        self.clipboard = ClipboardSync(self._clip_local) if clipboard else None
        self._chan = None
        self._stop = threading.Event()

    def start(self):
        self._wifi = wifi.low_latency()
        if self.clipboard:
            self.clipboard.start()
        threading.Thread(target=self.run, name="client", daemon=True).start()

    def stop(self):
        log.info("Cliente parado")
        self._stop.set()
        if self._chan:
            self._chan.close()
        if self.clipboard:
            self.clipboard.stop()
        if self._wifi:
            self._wifi.close()

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
                self.rtt = None
                self._rtts.clear()
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
            self.server_name = reply.get("server")
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
        started = time.monotonic()
        warned = False
        while True:
            if not warned and not self._rtts and time.monotonic() - started > NO_PONG_WARN:
                warned = True
                log.warning("O servidor não responde ao ping deste PC. Confira se os dois PCs estão "
                            "na mesma versão do MultiDesk.")
            try:
                chan.send({"t": "ping", "ts": time.monotonic()})
            except OSError:
                return
            if done.wait(PING_INTERVAL):
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
                content = parse_clip(msg)
                if content:  # em outra thread: colar uma imagem não atrasa o mouse
                    threading.Thread(target=self.clipboard.set, args=content, daemon=True).start()
            elif t == "ping":
                self._chan.send({"t": "pong", "ts": msg.get("ts")})
                # Sem medição própria recente, mostra o ping que o servidor mediu.
                theirs = msg.get("rtt")
                if isinstance(theirs, (int, float)) and time.monotonic() - self._last_pong > 3 * PING_INTERVAL:
                    self.rtt = theirs
                    self.on_change()
            elif t == "pong" and isinstance(msg.get("ts"), (int, float)):
                self._last_pong = time.monotonic()
                self._rtts.append((self._last_pong - msg["ts"]) * 1000)
                self.rtt = statistics.median(self._rtts)
                self.on_change()
        except Exception:
            log.exception("erro executando %s", t)

    def _clip_local(self, kind, data):
        chan = self._chan
        if chan and self.connected:
            try:
                chan.send(clip_message(kind, data))
            except OSError:
                pass


class FatalError(Exception):
    pass
