"""Servidor: o computador com o mouse/teclado físicos.

Quando o cursor encosta numa borda que tem um cliente, a entrada local é
suprimida e passa a ser enviada para ele. Ao voltar pela borda oposta da
tela do cliente, o controle volta para cá.
"""

import logging
import queue
import socket
import statistics
import threading
import time
from collections import deque

from . import platform, wifi
from .clipboard import ClipboardSync, clip_message, parse_clip
from .crypto import AuthError, handshake
from .link import DEFAULT_PORT, local_ip, make_link, new_code

log = logging.getLogger("multidesk.server")

SIDES = ("right", "left", "top", "bottom")
SIDE_PT = {"right": "direita", "left": "esquerda", "top": "acima", "bottom": "abaixo"}
PING_INTERVAL = 0.5
PING_SAMPLES = 5  # o ping mostrado é a mediana das últimas medições
TIMEOUT = 10.0
MOVE_INTERVAL = 1 / 250  # no máximo 250 posições do cursor por segundo
KEEPALIVE = 0.04  # enquanto controla um PC, nunca fica mais que isso sem mandar nada


def _coalesce_moves(msgs):
    """Troca cada sequência de 'move' seguidos só pelo último (mantém a ordem
    em relação a cliques e teclas)."""
    out = []
    for m in msgs:
        if out and m.get("t") == "move" and out[-1].get("t") == "move":
            out[-1] = m
        else:
            out.append(m)
    return out


class Remote:
    """Um cliente conectado."""

    def __init__(self, server, chan, addr, name, bounds, side):
        self.server = server
        self.chan = chan
        self.addr = addr
        self.name = name
        self.bounds = bounds  # (x, y, w, h) da tela do cliente
        self.side = side
        self.alive = True
        self.rtt = None  # ping em ms (ida e volta)
        self._rtts = deque(maxlen=PING_SAMPLES)
        self._q = queue.Queue()

    def start(self):
        threading.Thread(target=self._writer, name=f"tx-{self.name}", daemon=True).start()
        threading.Thread(target=self._reader, name=f"rx-{self.name}", daemon=True).start()

    def send(self, msg):
        if self.alive:
            self._q.put(msg)

    def close(self):
        if self.alive:
            self.alive = False
            self._q.put(None)
            self.chan.close()

    def _drain(self, batch):
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                return

    def _writer(self):
        # Fila + thread própria: o hook de entrada nunca espera pela rede.
        # Um mouse manda até 1000 eventos/s; mandar cada um num pacote enche a
        # rede (principalmente Wi-Fi) e cria fila, que aparece como lag e ping
        # alto. Então movimentos seguidos viram um só (vale a última posição)
        # e saem no máximo a cada MOVE_INTERVAL.
        next_ping = 0.0
        last_move = 0.0
        last_sent = 0.0
        try:
            while self.alive:
                now = time.monotonic()
                active = self.server.active is self
                if now >= next_ping:
                    # Manda junto o ping medido aqui: o cliente mostra este se
                    # não conseguir medir o dele.
                    self.chan.send({"t": "ping", "ts": now, "rtt": self.rtt})
                    last_sent = now
                    next_ping = now + PING_INTERVAL
                elif active and now - last_sent >= KEEPALIVE * 0.8:  # a espera pode acordar antes
                    # O Wi-Fi do outro PC "dorme" entre pacotes para economizar
                    # bateria, e o próximo clique/movimento espera ele acordar
                    # (50-100 ms). Enquanto controlamos esse PC, um pacotinho
                    # a cada KEEPALIVE o mantém acordado.
                    self.chan.send({"t": "k"})
                    last_sent = now
                wait = next_ping - now
                if active:
                    wait = min(wait, last_sent + KEEPALIVE - now)
                try:
                    msg = self._q.get(timeout=max(wait, 0.005))
                except queue.Empty:
                    continue
                batch = [msg]
                self._drain(batch)
                if all(m is not None and m.get("t") == "move" for m in batch):
                    wait = last_move + MOVE_INTERVAL - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                        self._drain(batch)
                stop = None in batch
                if stop:
                    batch = batch[:batch.index(None)]
                out = _coalesce_moves(batch)
                if out:
                    self.chan.send_many(out)
                    last_sent = time.monotonic()
                    if out[-1].get("t") == "move":
                        last_move = last_sent
                if stop:
                    break
        except OSError:
            pass
        self.server._drop(self)

    def _reader(self):
        try:
            while self.alive:
                msg = self.chan.recv()
                t = msg.get("t")
                if t == "clip":
                    self.server._clip_from(self, msg)
                elif t == "ping":
                    # Direto, sem passar pela fila: senão o ping mede a fila
                    # de movimentos em vez da rede.
                    self.chan.send({"t": "pong", "ts": msg.get("ts")})
                elif t == "pong" and isinstance(msg.get("ts"), (int, float)):
                    self._rtts.append((time.monotonic() - msg["ts"]) * 1000)
                    self.rtt = statistics.median(self._rtts)
                    self.server.on_change()
        except (OSError, ConnectionError, ValueError) as e:
            log.debug("leitura de %s terminou: %s", self.name, e)
        except Exception:
            log.exception("erro lendo de %s", self.name)
        self.server._drop(self)


class Server:
    def __init__(self, port=DEFAULT_PORT, code=None, layout=None, clipboard=True, on_change=None):
        self.port = port
        self.code = code or new_code()
        self.layout = dict(layout or {})  # nome do cliente -> lado
        self.on_change = on_change or (lambda: None)
        self.remotes = {}  # lado -> Remote
        self.active = None
        self.locked = False
        self.vx = self.vy = 0.0
        self._lock = threading.RLock()
        self._sock = None
        self._running = False
        self._wifi = None
        self.bounds = platform.screen_bounds()
        self.capture = platform.Capture(self)
        self.clipboard = ClipboardSync(self._clip_local) if clipboard else None

    @property
    def link(self):
        return make_link(local_ip(), self.port, self.code)

    # ---- ciclo de vida -------------------------------------------------

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.listen()
        self._running = True
        self._wifi = wifi.low_latency()
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()
        self.capture.start()
        if self.clipboard:
            self.clipboard.start()
        log.info("Servidor ouvindo na porta %d. Tela local: %dx%d", self.port, *self.bounds[2:])
        log.info("Link para os outros PCs: %s", self.link)
        log.info("Scroll Lock prende/solta o cursor na tela atual.")

    def stop(self):
        self._running = False
        with self._lock:
            if self.active:
                self._leave(self._local_center())
            for r in list(self.remotes.values()):
                r.close()
            self.remotes.clear()
        self.capture.stop()
        if self.clipboard:
            self.clipboard.stop()
        if self._sock:
            self._sock.close()
        if self._wifi:
            self._wifi.close()
        log.info("Servidor parado: mouse e teclado não são mais compartilhados")
        self.on_change()

    # ---- conexões ------------------------------------------------------

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_new, args=(conn, addr), daemon=True).start()

    def _handle_new(self, conn, addr):
        ip = addr[0]
        try:
            conn.settimeout(TIMEOUT)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            chan = handshake(conn, self.code, is_server=True)
            hello = chan.recv()
            if hello.get("t") != "hello":
                raise ConnectionError("mensagem inicial inesperada")
            name = str(hello.get("name") or ip)[:64]
            bounds = tuple(int(v) for v in hello["bounds"])
        except AuthError:
            log.warning("%s tentou conectar com o código errado", ip)
            conn.close()
            return
        except Exception as e:
            log.warning("Falha no handshake com %s: %s", ip, e)
            conn.close()
            return

        with self._lock:
            side = self._pick_side(name, hello.get("side"))
            if side is None:
                chan.send({"t": "error", "msg": "Não há lado livre no servidor (máximo 4 clientes)."})
                chan.close()
                return
            old = self.remotes.get(side)
            if old and old.name == name:
                self._drop(old)  # mesmo PC reconectando
            elif old:
                chan.send({"t": "error", "msg": f"O lado '{side}' já está ocupado por {old.name}."})
                chan.close()
                return
            remote = Remote(self, chan, addr, name, bounds, side)
            self.remotes[side] = remote
        chan.send({"t": "welcome", "side": side, "server": socket.gethostname()})
        remote.start()
        log.info("%s (%s) conectou — fica à %s desta tela, %dx%d",
                 name, ip, SIDE_PT[side], bounds[2], bounds[3])
        self.on_change()

    def _pick_side(self, name, wanted):
        if name in self.layout:
            return self.layout[name]
        taken = set(self.remotes) | set(self.layout.values())
        old = next((s for s, r in self.remotes.items() if r.name == name), None)
        if old:
            return old
        if wanted in SIDES and (wanted not in taken):
            return wanted
        return next((s for s in SIDES if s not in taken), None)

    def _drop(self, remote):
        with self._lock:
            if self.remotes.get(remote.side) is not remote:
                return
            # Primeiro devolve o controle, depois tira da lista: quem lê sem a
            # trava (a janela) nunca vê "nenhum PC" com um PC ainda no controle.
            if self.active is remote:
                log.warning("%s caiu enquanto estava no controle; voltando para cá", remote.name)
                self._leave(self._local_center())
            del self.remotes[remote.side]
        remote.close()
        log.info("%s desconectou", remote.name)
        self.on_change()

    # ---- área de transferência ------------------------------------------

    def _clip_local(self, kind, data):
        msg = clip_message(kind, data)
        for r in list(self.remotes.values()):
            r.send(msg)

    def _clip_from(self, origin, msg):
        content = parse_clip(msg)
        if not content:
            return
        if self.clipboard:
            self.clipboard.set(*content)
        for r in list(self.remotes.values()):  # repassa para os outros clientes
            if r is not origin:
                r.send(clip_message(*content))

    # ---- entrada (chamado pelos hooks) ----------------------------------

    def _local_center(self):
        x, y, w, h = self.bounds
        return x + w // 2, y + h // 2

    def on_move(self, x, y):
        if self.locked or not self.remotes:
            return
        x0, y0, w, h = self.bounds
        if x >= x0 + w - 1:
            side = "right"
        elif x <= x0:
            side = "left"
        elif y <= y0:
            side = "top"
        elif y >= y0 + h - 1:
            side = "bottom"
        else:
            return
        with self._lock:
            remote = self.remotes.get(side)
            if remote and not self.active:
                self._enter(remote, x, y)

    def _enter(self, remote, x, y):
        x0, y0, w, h = self.bounds
        cx, cy, cw, ch = remote.bounds
        fx = (x - x0) / max(w - 1, 1)
        fy = (y - y0) / max(h - 1, 1)
        if remote.side in ("right", "left"):
            self.vx = cx if remote.side == "right" else cx + cw - 1
            self.vy = cy + fy * (ch - 1)
        else:
            self.vy = cy if remote.side == "bottom" else cy + ch - 1
            self.vx = cx + fx * (cw - 1)
        if not self.capture.set_remote(True, self._local_center()):
            return
        self.active = remote
        remote.send({"t": "enter", "x": int(self.vx), "y": int(self.vy)})
        log.info("Controlando %s", remote.name)
        self.on_change()

    def on_delta(self, dx, dy):
        with self._lock:
            remote = self.active
            if not remote:
                return
            cx, cy, cw, ch = remote.bounds
            self.vx += dx
            self.vy += dy
            side = remote.side
            if not self.locked and (
                (side == "right" and self.vx < cx) or (side == "left" and self.vx > cx + cw - 1)
                or (side == "bottom" and self.vy < cy) or (side == "top" and self.vy > cy + ch - 1)
            ):
                self._leave(self._return_point(remote))
                return
            self.vx = min(max(self.vx, cx), cx + cw - 1)
            self.vy = min(max(self.vy, cy), cy + ch - 1)
            remote.send({"t": "move", "x": int(self.vx), "y": int(self.vy)})

    def _return_point(self, remote):
        x0, y0, w, h = self.bounds
        cx, cy, cw, ch = remote.bounds
        fx = (self.vx - cx) / max(cw - 1, 1)
        fy = (self.vy - cy) / max(ch - 1, 1)
        lx = x0 + min(max(fx, 0), 1) * (w - 1)
        ly = y0 + min(max(fy, 0), 1) * (h - 1)
        if remote.side == "right":
            lx = x0 + w - 3
        elif remote.side == "left":
            lx = x0 + 2
        elif remote.side == "bottom":
            ly = y0 + h - 3
        else:
            ly = y0 + 2
        return int(lx), int(ly)

    def _leave(self, local_pos):
        remote, self.active = self.active, None
        self.capture.set_remote(False, local_pos)
        if remote:
            remote.send({"t": "leave"})
            log.info("Controle de volta neste computador")
        self.on_change()

    def on_button(self, name, down):
        remote = self.active
        if remote:
            remote.send({"t": "button", "b": name, "down": down})

    def on_scroll(self, dx, dy):
        remote = self.active
        if remote:
            remote.send({"t": "scroll", "dx": dx, "dy": dy})

    def on_key(self, desc, down):
        if desc.get("k") == "scroll_lock":
            if down:
                self.locked = not self.locked
                log.info("Cursor %s", "PRESO nesta tela" if self.locked else "livre")
            return
        remote = self.active
        if remote:
            remote.send({"t": "key", "key": desc, "down": down})
