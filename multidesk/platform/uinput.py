"""Dispositivos virtuais de entrada do kernel Linux (/dev/uinput), sem dependências.

É o mesmo caminho que um mouse/teclado USB de verdade: funciona no Wayland
(GNOME, KDE, Hyprland, Sway, niri...), no X11 e até no console. Os pacotes
(.deb e pacman) instalam uma regra udev que dá acesso ao /dev/uinput para o usuário logado.
"""

import fcntl
import os
import struct

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
REL_HWHEEL, REL_WHEEL = 0x06, 0x08
ABS_X, ABS_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114
ABS_MAX = 65535

_UI_SET_EVBIT = 0x40045564
_UI_SET_KEYBIT = 0x40045565
_UI_SET_RELBIT = 0x40045566
_UI_SET_ABSBIT = 0x40045567
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502
_BUS_VIRTUAL = 0x06

_EVENT = struct.Struct("@llHHi")  # struct input_event


class Device:
    def __init__(self, name, keys=(), rels=(), abs_axes=()):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            for ev_type, codes, bit in ((EV_KEY, keys, _UI_SET_KEYBIT), (EV_REL, rels, _UI_SET_RELBIT),
                                        (EV_ABS, abs_axes, _UI_SET_ABSBIT)):
                if codes:
                    fcntl.ioctl(self.fd, _UI_SET_EVBIT, ev_type)
                    for code in codes:
                        fcntl.ioctl(self.fd, bit, code)
            absmax = [0] * 64
            for axis in abs_axes:
                absmax[axis] = ABS_MAX
            # struct uinput_user_dev (interface antiga, aceita por todos os kernels)
            setup = struct.pack("80sHHHHi", name.encode()[:79], _BUS_VIRTUAL, 0x4D44, 0x0001, 1, 0)
            setup += struct.pack("64i", *absmax) + struct.pack("64i", *[0] * 64) * 3
            os.write(self.fd, setup)
            fcntl.ioctl(self.fd, _UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def emit(self, events):
        data = b"".join(_EVENT.pack(0, 0, t, c, v) for t, c, v in events)
        os.write(self.fd, data + _EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        try:
            fcntl.ioctl(self.fd, _UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


def keyboard():
    # Todas as teclas de teclado do kernel (1..247), sem os botões de mouse/joystick.
    return Device("MultiDesk Keyboard", keys=range(1, 248))


def pointer():
    # Eixos absolutos + botões, como o "tablet" de máquinas virtuais: o
    # compositor posiciona o cursor direto na coordenada, sem aceleração.
    return Device("MultiDesk Pointer", keys=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA),
                  rels=(REL_WHEEL, REL_HWHEEL), abs_axes=(ABS_X, ABS_Y))
