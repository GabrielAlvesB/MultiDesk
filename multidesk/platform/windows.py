"""Backend Windows: hooks de baixo nível (via pynput) e SendInput."""

import ctypes
import logging
from ctypes import wintypes

from pynput import keyboard, mouse

log = logging.getLogger("multidesk.win")

OS = "win"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
user32.VkKeyScanW.restype = ctypes.c_short
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_MOUSEMOVE = 0x200
_BUTTON_MSGS = {
    0x201: ("left", True), 0x202: ("left", False),
    0x204: ("right", True), 0x205: ("right", False),
    0x207: ("middle", True), 0x208: ("middle", False),
}
WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEHWHEEL = 0x20A, 0x20B, 0x20C, 0x20E
LLMHF_INJECTED = 0x01
LLKHF_INJECTED = 0x10
MAPVK_VK_TO_CHAR = 2

# vk -> nome da tecla especial (nomes do pynput, iguais no Windows e no Linux).
SPECIAL_VK = {k.value.vk: k.name for k in keyboard.Key if k.value.vk is not None}
SPECIAL_VK.update({
    0x10: "shift", 0x11: "ctrl", 0x12: "alt",
    0xA0: "shift_l", 0xA1: "shift_r", 0xA2: "ctrl_l", 0xA3: "ctrl_r",
    0xA4: "alt_l", 0xA5: "alt_gr", 0x5B: "cmd_l", 0x5C: "cmd_r",
})

_BUTTONS = {
    "left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle,
    "x1": mouse.Button.x1, "x2": mouse.Button.x2,
}


def init_process():
    """Coordenadas em pixels reais mesmo com escala de tela (125%, 150%...)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_bounds():
    gsm = user32.GetSystemMetrics
    return gsm(76), gsm(77), gsm(78), gsm(79)  # SM_X/Y/CX/CYVIRTUALSCREEN


def key_desc(vk):
    name = SPECIAL_VK.get(vk)
    if name:
        return {"k": name}
    ch = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) & 0x7FFF  # bit alto = tecla morta
    return {"vk": vk, "ch": chr(ch).lower() if ch else None, "os": OS}


class Capture:
    def __init__(self, sink):
        self.sink = sink
        self.remote = False
        self._center = (0, 0)
        self._ml = mouse.Listener(win32_event_filter=self._mouse_filter)
        self._kl = keyboard.Listener(win32_event_filter=self._kbd_filter)

    def start(self):
        self._ml.start()
        self._kl.start()

    def stop(self):
        self.remote = False
        self._ml.stop()
        self._kl.stop()

    def set_remote(self, remote, pos):
        """Entra/sai do modo remoto. `pos` = onde deixar o cursor local."""
        self.remote = remote
        if remote:
            self._center = pos
        user32.SetCursorPos(int(pos[0]), int(pos[1]))
        return True

    def _mouse_filter(self, msg, data):
        if data.flags & LLMHF_INJECTED:
            return True
        was_remote = self.remote
        try:
            if msg == WM_MOUSEMOVE:
                if was_remote:
                    # Com o evento suprimido o cursor fica parado no centro,
                    # então a diferença para o centro é o movimento do mouse.
                    dx, dy = data.pt.x - self._center[0], data.pt.y - self._center[1]
                    if dx or dy:
                        self.sink.on_delta(dx, dy)
                else:
                    self.sink.on_move(data.pt.x, data.pt.y)
            elif was_remote:
                if msg in _BUTTON_MSGS:
                    self.sink.on_button(*_BUTTON_MSGS[msg])
                elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    name = "x1" if (data.mouseData >> 16) == 1 else "x2"
                    self.sink.on_button(name, msg == WM_XBUTTONDOWN)
                elif msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                    notches = wintypes.SHORT(data.mouseData >> 16).value / 120
                    if msg == WM_MOUSEWHEEL:
                        self.sink.on_scroll(0, notches)
                    else:
                        self.sink.on_scroll(notches, 0)
        except Exception:
            log.exception("erro tratando evento de mouse")
        # Suprime também o evento que causou a troca de tela, senão o Windows
        # move o cursor por cima do SetCursorPos que acabamos de fazer.
        if was_remote or self.remote:
            self._ml.suppress_event()
        return True

    def _kbd_filter(self, msg, data):
        if data.flags & LLKHF_INJECTED:
            return True
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        try:
            self.sink.on_key(key_desc(data.vkCode), down)
        except Exception:
            log.exception("erro tratando evento de teclado")
        if self.remote:
            self._kl.suppress_event()
        return True


class Injector:
    def __init__(self):
        self.mouse = mouse.Controller()
        self.kb = keyboard.Controller()
        self._held_keys = set()
        self._held_buttons = set()

    def move(self, x, y):
        self.mouse.position = (x, y)

    def button(self, name, down):
        btn = _BUTTONS.get(name)
        if btn is None:
            return
        if down:
            self.mouse.press(btn)
            self._held_buttons.add(btn)
        else:
            self.mouse.release(btn)
            self._held_buttons.discard(btn)

    def scroll(self, dx, dy):
        self.mouse.scroll(dx, dy)  # pynput multiplica por WHEEL_DELTA, aceita fração

    def _resolve(self, desc):
        name = desc.get("k")
        if name:
            return getattr(keyboard.Key, name, None)
        if desc.get("os") == OS and desc.get("vk"):
            return keyboard.KeyCode.from_vk(desc["vk"])
        ch = desc.get("ch")
        if not ch:
            return None
        # Aperta a tecla física que gera esse caractere (o Shift já vem como
        # evento próprio do outro PC). Se só sai com Ctrl/AltGr, digita o
        # caractere direto via Unicode para não virar outra letra.
        scan = user32.VkKeyScanW(ch)
        if scan != -1 and (scan & 0xFF) != 0xFF and not (scan >> 8) & 0x6:
            return keyboard.KeyCode.from_vk(scan & 0xFF)
        return keyboard.KeyCode.from_char(ch)

    def key(self, desc, down):
        key = self._resolve(desc)
        if key is None:
            return
        if down:
            self.kb.press(key)
            self._held_keys.add(key)
        else:
            self.kb.release(key)
            self._held_keys.discard(key)

    def release_all(self):
        for key in list(self._held_keys):
            try:
                self.kb.release(key)
            except Exception:
                pass
        for btn in list(self._held_buttons):
            try:
                self.mouse.release(btn)
            except Exception:
                pass
        self._held_keys.clear()
        self._held_buttons.clear()
