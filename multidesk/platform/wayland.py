"""Leitura dos monitores direto do compositor Wayland, sem dependências.

Fala o protocolo do Wayland pelo socket da sessão (como qualquer aplicativo)
e pergunta a posição e o tamanho lógico de cada monitor (xdg-output). Assim o
MultiDesk sabe o tamanho da área de trabalho em qualquer compositor (KDE, GNOME,
Hyprland, Sway, niri, COSMIC...) mesmo sem XWayland.
"""

import os
import socket
import struct

_HEADER = struct.Struct("=II")  # objeto, (tamanho << 16) | opcode
_DISPLAY = 1


class _Conn:
    def __init__(self):
        name = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = name if name.startswith("/") else os.path.join(os.environ["XDG_RUNTIME_DIR"], name)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(path)
        self.buf = b""
        self.next_id = 2

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    def send(self, obj, opcode, payload=b""):
        self.sock.sendall(_HEADER.pack(obj, (_HEADER.size + len(payload)) << 16 | opcode) + payload)

    def events(self):
        while True:
            while len(self.buf) >= _HEADER.size:
                obj, word = _HEADER.unpack_from(self.buf)
                opcode, size = word & 0xFFFF, word >> 16
                if len(self.buf) < size:
                    break
                body, self.buf = self.buf[_HEADER.size:size], self.buf[size:]
                yield obj, opcode, body
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("compositor fechou a conexão")
            self.buf += chunk

    def roundtrip(self, handle):
        """Espera o compositor responder tudo o que foi pedido até agora."""
        cb = self.new_id()
        self.send(_DISPLAY, 0, struct.pack("=I", cb))  # wl_display.sync
        for obj, opcode, body in self.events():
            if obj == cb:
                return
            if obj == _DISPLAY and opcode == 0:  # wl_display.error
                raise OSError("erro do compositor: " + _string(body, 8)[0])
            handle(obj, opcode, body)


def _string(body, offset):
    (n,) = struct.unpack_from("=I", body, offset)
    raw = body[offset + 4:offset + 4 + n]
    return raw.rstrip(b"\0").decode(errors="replace"), offset + 4 + (n + 3) // 4 * 4


def _pack_string(s):
    raw = s.encode() + b"\0"
    return struct.pack("=I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _registry(conn):
    """Cria o wl_registry e devolve (id dele, [(nome, interface, versão)])."""
    registry = conn.new_id()
    conn.send(_DISPLAY, 1, struct.pack("=I", registry))  # wl_display.get_registry
    globals_ = []

    def on_global(obj, opcode, body):
        if obj == registry and opcode == 0:
            (name,) = struct.unpack_from("=I", body)
            iface, off = _string(body, 4)
            (version,) = struct.unpack_from("=I", body, off)
            globals_.append((name, iface, version))

    conn.roundtrip(on_global)
    return registry, globals_


def interfaces():
    """Nomes dos protocolos que o compositor oferece."""
    conn = _Conn()
    try:
        return {iface for _, iface, _ in _registry(conn)[1]}
    finally:
        conn.sock.close()


def outputs():
    """Lista de (x, y, largura, altura) lógicos, um por monitor."""
    conn = _Conn()
    try:
        registry, globals_ = _registry(conn)

        def bind(name, iface, version):
            oid = conn.new_id()
            conn.send(registry, 0, struct.pack("=I", name) + _pack_string(iface) + struct.pack("=II", version, oid))
            return oid

        outs = {}  # id do wl_output -> dados
        manager = None
        for name, iface, version in globals_:
            if iface == "wl_output":
                outs[bind(name, iface, min(version, 2))] = {"scale": 1}
            elif iface == "zxdg_output_manager_v1":
                manager = bind(name, iface, min(version, 3))
        xdg = {}  # id do zxdg_output -> dados do wl_output correspondente
        if manager:
            for oid, data in outs.items():
                xid = conn.new_id()
                conn.send(manager, 1, struct.pack("=II", xid, oid))  # get_xdg_output
                xdg[xid] = data

        def on_output(obj, opcode, body):
            if obj in outs:
                data = outs[obj]
                if opcode == 0:  # geometry
                    data["x"], data["y"] = struct.unpack_from("=ii", body)
                    _, off = _string(body, 20)
                    _, off = _string(body, off)
                    (data["transform"],) = struct.unpack_from("=i", body, off)
                elif opcode == 1:  # mode
                    flags, w, h, _ = struct.unpack_from("=Iiii", body)
                    if flags & 1:  # modo atual
                        data["mode"] = (w, h)
                elif opcode == 3:  # scale
                    (data["scale"],) = struct.unpack_from("=i", body)
            elif obj in xdg:
                if opcode == 0:
                    xdg[obj]["lpos"] = struct.unpack_from("=ii", body)
                elif opcode == 1:
                    xdg[obj]["lsize"] = struct.unpack_from("=ii", body)

        conn.roundtrip(on_output)
        conn.roundtrip(on_output)  # alguns compositores mandam o xdg-output depois do wl_output

        rects = []
        for data in outs.values():
            if "lpos" in data and "lsize" in data:
                rects.append((*data["lpos"], *data["lsize"]))
            elif "mode" in data:  # sem xdg-output: estima pelo modo e pela escala
                w, h = data["mode"]
                if data.get("transform", 0) % 2:  # girado 90/270 graus
                    w, h = h, w
                s = max(data["scale"], 1)
                rects.append((data.get("x", 0), data.get("y", 0), w // s, h // s))
        return [r for r in rects if r[2] > 0 and r[3] > 0]
    finally:
        conn.sock.close()


def desktop_bounds():
    """(x, y, largura, altura) da área que cobre todos os monitores, ou None."""
    rects = outputs()
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1 - x0, y1 - y0
