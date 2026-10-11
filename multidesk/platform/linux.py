"""Backend Linux (X11): RECORD via pynput, grab do X para suprimir e XTest para simular.

Precisa de sessão X11 ("Ubuntu on Xorg" na tela de login). No Wayland o
sistema bloqueia captura e simulação de entrada por programas comuns.
"""

import logging
import os
import threading
import time

import Xlib.display
import Xlib.ext.xtest
import Xlib.XK
from Xlib import X
from pynput import keyboard, mouse

log = logging.getLogger("multidesk.linux")

OS = "linux"

_BUTTON_NAMES = {
    mouse.Button.left: "left", mouse.Button.right: "right", mouse.Button.middle: "middle",
    getattr(mouse.Button, "button8"): "x1", getattr(mouse.Button, "button9"): "x2",
}
_BUTTONS = {v: k for k, v in _BUTTON_NAMES.items()}

# Caracteres que no Linux costumam ficar em teclas mortas (teclado ABNT2 etc.).
_DEAD_KEYSYMS = {
    "´": "dead_acute", "`": "dead_grave", "~": "dead_tilde",
    "^": "dead_circumflex", "¨": "dead_diaeresis", "'": "dead_acute",
}


def init_process():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        log.warning("Sessão Wayland detectada: o MultiDesk precisa de X11. "
                    "Saia da sessão e escolha 'Ubuntu on Xorg' (ou equivalente) no login.")


def screen_bounds():
    d = Xlib.display.Display()
    try:
        s = d.screen()
        return 0, 0, s.width_in_pixels, s.height_in_pixels
    finally:
        d.close()


def key_desc(key):
    if isinstance(key, keyboard.Key):
        return {"k": key.name}
    return {"vk": getattr(key, "vk", None), "ch": getattr(key, "char", None), "os": OS}


def char_to_keysym(ch):
    if len(ch) != 1:
        return 0
    code = ord(ch)
    if code < 0x100:
        return code  # Latin-1: keysym == código do caractere
    return 0x01000000 | code


class _Grabber:
    """Conexão X própria para capturar ponteiro/teclado e mover o cursor."""

    def __init__(self):
        self.d = Xlib.display.Display()
        self.root = self.d.screen().root
        self.lock = threading.Lock()
        pix = self.root.create_pixmap(1, 1, 1)
        gc = pix.create_gc(foreground=0, background=0)
        pix.fill_rectangle(gc, 0, 0, 1, 1)
        self.blank = pix.create_cursor(pix, (0, 0, 0), (0, 0, 0), 0, 0)
        self._stop = False
        threading.Thread(target=self._drain, name="x-grab", daemon=True).start()

    def _drain(self):
        # Eventos entregues por causa do grab precisam ser lidos e descartados.
        while not self._stop:
            with self.lock:
                while self.d.pending_events():
                    self.d.next_event()
            time.sleep(0.01)

    def grab(self):
        mask = X.PointerMotionMask | X.ButtonPressMask | X.ButtonReleaseMask
        with self.lock:
            for _ in range(10):
                if self.root.grab_pointer(False, mask, X.GrabModeAsync, X.GrabModeAsync,
                                          X.NONE, self.blank, X.CurrentTime) == X.GrabSuccess:
                    break
                time.sleep(0.02)
            else:
                return False
            for _ in range(10):
                if self.root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync,
                                           X.CurrentTime) == X.GrabSuccess:
                    return True
                time.sleep(0.02)
            self.d.ungrab_pointer(X.CurrentTime)
            self.d.flush()
            return False

    def ungrab(self):
        with self.lock:
            self.d.ungrab_pointer(X.CurrentTime)
            self.d.ungrab_keyboard(X.CurrentTime)
            self.d.flush()

    def warp(self, x, y):
        with self.lock:
            self.root.warp_pointer(int(x), int(y))
            self.d.flush()

    def close(self):
        self._stop = True


class Capture:
    def __init__(self, sink):
        self.sink = sink
        self.remote = False
        self._grabber = _Grabber()
        _, _, w, h = screen_bounds()
        self._limit = (w // 4, h // 4)
        self._center = (w // 2, h // 2)
        self._last = self._center
        self._ml = mouse.Listener(on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)

    def start(self):
        self._ml.start()
        self._kl.start()

    def stop(self):
        if self.remote:
            self._grabber.ungrab()
        self.remote = False
        self._ml.stop()
        self._kl.stop()
        self._grabber.close()

    def set_remote(self, remote, pos):
        if remote:
            if not self._grabber.grab():
                log.warning("Não consegui capturar mouse/teclado (outro programa está segurando)")
                return False
            self._center = (int(pos[0]), int(pos[1]))
            self._last = self._center
            self.remote = True
        else:
            self.remote = False
            self._grabber.ungrab()
        self._grabber.warp(*pos)
        return True

    def _on_move(self, x, y):
        try:
            if not self.remote:
                self.sink.on_move(x, y)
                return
            dx, dy = x - self._last[0], y - self._last[1]
            if abs(dx) > self._limit[0] or abs(dy) > self._limit[1]:
                return  # evento antigo de antes do último warp
            self._last = (x, y)
            if dx or dy:
                self.sink.on_delta(dx, dy)
            if self.remote and (abs(x - self._center[0]) > self._limit[0] // 2
                                or abs(y - self._center[1]) > self._limit[1] // 2):
                self._grabber.warp(*self._center)
                self._last = self._center
        except Exception:
            log.exception("erro tratando movimento")

    def _on_click(self, x, y, button, pressed):
        name = _BUTTON_NAMES.get(button)
        if self.remote and name:
            self.sink.on_button(name, pressed)

    def _on_scroll(self, x, y, dx, dy):
        if self.remote:
            self.sink.on_scroll(dx, dy)

    def _on_press(self, key):
        if key is not None:
            self.sink.on_key(key_desc(key), True)

    def _on_release(self, key):
        if key is not None:
            self.sink.on_key(key_desc(key), False)


class Injector:
    def __init__(self):
        self.mouse = mouse.Controller()
        self.kb = keyboard.Controller()
        self.d = Xlib.display.Display()
        self._lock = threading.Lock()
        self._scroll_acc = [0.0, 0.0]
        self._held_keys = set()     # teclas especiais (pynput Key)
        self._held_codes = set()    # keycodes X enviados direto
        self._held_buttons = set()

    def move(self, x, y):
        self.mouse.position = (int(x), int(y))

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
        # X11 só tem "cliques" inteiros; acumula as frações (touchpad do Windows).
        self._scroll_acc[0] += dx
        self._scroll_acc[1] += dy
        ix, iy = int(self._scroll_acc[0]), int(self._scroll_acc[1])
        self._scroll_acc[0] -= ix
        self._scroll_acc[1] -= iy
        if ix or iy:
            self.mouse.scroll(ix, iy)

    def _keycode(self, desc):
        keysym = 0
        if desc.get("os") == OS and desc.get("vk"):
            keysym = desc["vk"]
        elif desc.get("ch"):
            keysym = char_to_keysym(desc["ch"])
        code = self.d.keysym_to_keycode(keysym) if keysym else 0
        if not code and desc.get("ch") in _DEAD_KEYSYMS:
            code = self.d.keysym_to_keycode(Xlib.XK.string_to_keysym(_DEAD_KEYSYMS[desc["ch"]]))
        if not code and keysym:
            code = self._spare_keycode(keysym)
        return code

    def _spare_keycode(self, keysym):
        """Caractere que não existe no layout deste PC (ex.: 'ç' num teclado
        americano): coloca o keysym numa tecla sem uso, como faz o xdotool."""
        if not hasattr(self, "_spare"):
            first = self.d.display.info.min_keycode
            count = self.d.display.info.max_keycode - first + 1
            mapping = self.d.get_keyboard_mapping(first, count)
            self._spare = [first + i for i, syms in enumerate(mapping) if not any(syms)][-4:]
            self._spare_map = {}
        if keysym in self._spare_map:
            return self._spare_map[keysym]
        if not self._spare:
            return 0
        code = self._spare[len(self._spare_map) % len(self._spare)]
        for k, c in list(self._spare_map.items()):
            if c == code:
                del self._spare_map[k]
        self.d.change_keyboard_mapping(code, [(keysym, keysym)])
        self.d.sync()
        self._spare_map[keysym] = code
        return code

    def key(self, desc, down):
        name = desc.get("k")
        if name:
            key = getattr(keyboard.Key, name, None)
            if key is None:
                return
            if down:
                self.kb.press(key)
                self._held_keys.add(key)
            else:
                self.kb.release(key)
                self._held_keys.discard(key)
            return
        with self._lock:
            code = self._keycode(desc)
            if not code:
                return
            Xlib.ext.xtest.fake_input(self.d, X.KeyPress if down else X.KeyRelease, code)
            self.d.sync()
        (self._held_codes.add if down else self._held_codes.discard)(code)

    def release_all(self):
        for key in list(self._held_keys):
            try:
                self.kb.release(key)
            except Exception:
                pass
        with self._lock:
            for code in list(self._held_codes):
                Xlib.ext.xtest.fake_input(self.d, X.KeyRelease, code)
            self.d.sync()
        for btn in list(self._held_buttons):
            try:
                self.mouse.release(btn)
            except Exception:
                pass
        self._held_keys.clear()
        self._held_codes.clear()
        self._held_buttons.clear()
