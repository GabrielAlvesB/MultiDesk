"""Área de transferência compartilhada: texto e imagens, nos dois sentidos.

Cada backend sabe: detectar que a área de transferência mudou (barato),
ler o conteúdo atual e escrever um conteúdo novo.

  Windows: API do sistema via ctypes; mudança pelo GetClipboardSequenceNumber.
           Imagens em "PNG" (mantém transparência) e CF_DIB (todos os apps).
  Linux:   wl-clipboard nos compositores Wayland com "data-control" (KDE,
           Hyprland, Sway, niri, COSMIC...): mudança avisada pelo wl-paste --watch.
           Nos outros (X11, e GNOME via XWayland, que espelha a área de
           transferência): xclip, com mudança por evento XFixes ou consulta periódica.

O conteúdo viaja como ("text", str) ou ("image", bytes PNG).
"""

import base64
import hashlib
import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

log = logging.getLogger("multidesk.clipboard")

MAX_TEXT = 4 * 1024 * 1024
MAX_IMAGE = 16 * 1024 * 1024  # PNG; maior que isso não é enviado


def fingerprint(kind, data):
    """Identidade do conteúdo, imune a reencodar a imagem ou trocar \\r\\n por \\n."""
    if kind == "text":
        return "t:" + hashlib.sha256(data.replace("\r\n", "\n").encode("utf-8", "replace")).hexdigest()
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            raw = im.convert("RGBA").tobytes()
            return f"i:{im.size}:" + hashlib.sha256(raw).hexdigest()
    except Exception:
        return "b:" + hashlib.sha256(data).hexdigest()


def clip_message(kind, data):
    """Mensagem de rede. O texto fica em "text" (compatível com versões antigas)."""
    if kind == "text":
        return {"t": "clip", "text": data}
    return {"t": "clip", "img": base64.b64encode(data).decode("ascii")}


def parse_clip(msg):
    if isinstance(msg.get("text"), str):
        return "text", msg["text"]
    if isinstance(msg.get("img"), str):
        try:
            return "image", base64.b64decode(msg["img"], validate=True)
        except ValueError:
            return None
    return None


def to_png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# Windows

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _name, _args, _res in (
        ("OpenClipboard", [wintypes.HWND], wintypes.BOOL),
        ("CloseClipboard", [], wintypes.BOOL),
        ("EmptyClipboard", [], wintypes.BOOL),
        ("GetClipboardData", [wintypes.UINT], wintypes.HANDLE),
        ("SetClipboardData", [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
        ("IsClipboardFormatAvailable", [wintypes.UINT], wintypes.BOOL),
        ("GetClipboardSequenceNumber", [], wintypes.DWORD),
        ("RegisterClipboardFormatW", [wintypes.LPCWSTR], wintypes.UINT),
        ("CreateWindowExW", [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
        ("DestroyWindow", [wintypes.HWND], wintypes.BOOL),
    ):
        getattr(_u32, _name).argtypes = _args
        getattr(_u32, _name).restype = _res
    for _name, _args, _res in (
        ("GlobalAlloc", [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
        ("GlobalLock", [wintypes.HGLOBAL], ctypes.c_void_p),
        ("GlobalUnlock", [wintypes.HGLOBAL], wintypes.BOOL),
        ("GlobalSize", [wintypes.HGLOBAL], ctypes.c_size_t),
        ("GlobalFree", [wintypes.HGLOBAL], wintypes.HGLOBAL),
    ):
        getattr(_k32, _name).argtypes = _args
        getattr(_k32, _name).restype = _res

    CF_UNICODETEXT, CF_DIB = 13, 8
    GMEM_MOVEABLE = 0x0002
    HWND_MESSAGE = wintypes.HWND(-3)

    class _Backend:
        name = "Windows"

        def __init__(self):
            self._png = _u32.RegisterClipboardFormatW("PNG")
            self._seq = _u32.GetClipboardSequenceNumber()
            self._own_seq = None

        def changed(self):
            seq = _u32.GetClipboardSequenceNumber()
            if seq == self._seq:
                return False
            self._seq = seq
            return seq != self._own_seq  # ignora a mudança que nós mesmos fizemos

        def _open(self, hwnd=None):
            for _ in range(20):  # outro programa pode estar com ela aberta
                if _u32.OpenClipboard(hwnd):
                    return True
                time.sleep(0.02)
            return False

        def read(self):
            if _u32.IsClipboardFormatAvailable(self._png) or _u32.IsClipboardFormatAvailable(CF_DIB):
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grabclipboard()
                    if img is not None and not isinstance(img, list):
                        return "image", to_png(img)
                except Exception as e:
                    log.debug("falha lendo imagem: %s", e)
            if _u32.IsClipboardFormatAvailable(CF_UNICODETEXT) and self._open():
                try:
                    h = _u32.GetClipboardData(CF_UNICODETEXT)
                    p = _k32.GlobalLock(h) if h else None
                    if p:
                        try:
                            return "text", ctypes.wstring_at(p)
                        finally:
                            _k32.GlobalUnlock(h)
                finally:
                    _u32.CloseClipboard()
            return None

        @staticmethod
        def _global(data):
            h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = _k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            _k32.GlobalUnlock(h)
            return h

        def write(self, kind, data):
            if kind == "text":
                items = [(CF_UNICODETEXT, data.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\0\0")]
            else:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    bmp = io.BytesIO()
                    im.convert("RGB").save(bmp, format="BMP")
                items = [(self._png, data), (CF_DIB, bmp.getvalue()[14:])]  # DIB = BMP sem o cabeçalho de arquivo
            # Janela temporária como "dona": destruída logo depois, os dados
            # continuam na área de transferência e ninguém fica esperando por nós.
            hwnd = _u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
            try:
                if not self._open(hwnd):
                    raise OSError("área de transferência ocupada por outro programa")
                try:
                    _u32.EmptyClipboard()
                    for fmt, payload in items:
                        h = self._global(payload)
                        if not _u32.SetClipboardData(fmt, h):
                            _k32.GlobalFree(h)
                finally:
                    _u32.CloseClipboard()
                self._own_seq = self._seq = _u32.GetClipboardSequenceNumber()
            finally:
                _u32.DestroyWindow(hwnd)


# --------------------------------------------------------------------------
# Linux

else:
    _IMAGE_TYPES = ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp", "image/tiff")

    def _image_to_png(target, data):
        if target == "image/png":
            return data
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return to_png(im)

    def _die_with_parent():
        """O wl-paste --watch não sai sozinho: morre junto com o MultiDesk."""
        try:
            import ctypes
            import signal
            ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
        except Exception:
            pass

    class _WlClipboard:
        name = "Linux (wl-clipboard)"

        def __init__(self):
            self._event = threading.Event()
            # Roda "echo" a cada mudança: cada linha na saída é um aviso.
            self._proc = subprocess.Popen(["wl-paste", "--watch", "echo", "."], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          preexec_fn=_die_with_parent)
            time.sleep(0.3)
            if self._proc.poll() is not None:
                raise OSError("wl-paste --watch não funciona neste compositor")
            threading.Thread(target=self._watch, name="clip-wl", daemon=True).start()

        def _watch(self):
            for _ in self._proc.stdout:
                self._event.set()
            log.debug("wl-paste --watch terminou")

        def changed(self):
            if self._event.is_set():
                self._event.clear()
                return True
            return False

        @staticmethod
        def _paste(*args, timeout=3):
            r = subprocess.run(["wl-paste", "--no-newline", *args], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
            return r.stdout if r.returncode == 0 else None

        def read(self):
            types = (self._paste("--list-types") or b"").decode(errors="ignore").splitlines()
            for t in _IMAGE_TYPES:
                if t in types:
                    data = self._paste("--type", t, timeout=10)
                    if data:
                        return "image", _image_to_png(t, data)
            for t in ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT"):
                if t in types:
                    data = self._paste("--type", t)
                    if data:
                        return "text", data.decode("utf-8", errors="replace")
            return None

        def write(self, kind, data):
            target, payload = ("text/plain;charset=utf-8", data.encode("utf-8")) if kind == "text" else ("image/png", data)
            # O wl-copy fica em segundo plano servindo o conteúdo até alguém copiar outra coisa.
            subprocess.run(["wl-copy", "--type", target], input=payload,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)

        def close(self):
            self._proc.terminate()

    class _XClipboard:
        name = "Linux (xclip)"

        def __init__(self):
            self._event = threading.Event()
            self._events_ok = self._watch_xfixes()
            self._last_poll = 0.0
            if not self._events_ok:
                log.info("XFixes indisponível: área de transferência por consulta periódica")

        def _watch_xfixes(self):
            """Recebe um evento do X toda vez que alguém copia algo."""
            try:
                import Xlib.display
                from Xlib.ext import xfixes
                d = Xlib.display.Display()
                if not d.has_extension("XFIXES"):
                    return False
                d.xfixes_query_version()
                win = d.screen().root.create_window(0, 0, 1, 1, 0, 0)  # janela invisível só para eventos
                d.xfixes_select_selection_input(win, d.intern_atom("CLIPBOARD"),
                                                xfixes.XFixesSetSelectionOwnerNotifyMask)
                d.flush()

                def loop():
                    while True:
                        d.next_event()
                        self._event.set()

                threading.Thread(target=loop, name="clip-xfixes", daemon=True).start()
                return True
            except Exception as e:
                log.debug("XFixes: %s", e)
                return False

        def changed(self):
            if self._events_ok:
                if self._event.is_set():
                    self._event.clear()
                    return True
                return False
            # Sem eventos: lê periodicamente e deixa o hash dizer se mudou.
            now = time.monotonic()
            if now - self._last_poll >= 1.0:
                self._last_poll = now
                return True
            return False

        @staticmethod
        def _xclip_out(target, timeout=3):
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o", "-t", target],
                               capture_output=True, timeout=timeout)
            return r.stdout if r.returncode == 0 else None

        def read(self):
            targets = (self._xclip_out("TARGETS") or b"").decode(errors="ignore").split()
            for t in _IMAGE_TYPES:
                if t in targets:
                    data = self._xclip_out(t, timeout=10)
                    if data:
                        return "image", _image_to_png(t, data)
            for t in ("UTF8_STRING", "text/plain;charset=utf-8", "STRING", "TEXT"):
                if t in targets:
                    data = self._xclip_out(t)
                    if data:
                        return "text", data.decode("utf-8", errors="replace")
            return None

        def write(self, kind, data):
            target, payload = ("UTF8_STRING", data.encode("utf-8")) if kind == "text" else ("image/png", data)
            # O xclip fica rodando em segundo plano servindo o conteúdo até
            # alguém copiar outra coisa; por isso a saída vai para DEVNULL.
            subprocess.run(["xclip", "-selection", "clipboard", "-t", target, "-i"], input=payload,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)

    def _Backend():
        """wl-clipboard se o compositor deixa ler a área de transferência sem
        janela em foco (protocolo data-control); senão xclip pelo X/XWayland."""
        why = []
        if os.environ.get("WAYLAND_DISPLAY"):
            if shutil.which("wl-paste") and shutil.which("wl-copy"):
                try:
                    from .platform import wayland
                    if wayland.interfaces() & {"zwlr_data_control_manager_v1", "ext_data_control_manager_v1"}:
                        return _WlClipboard()
                    why.append("o compositor não oferece data-control para o wl-clipboard")
                except Exception as e:
                    why.append(f"wl-clipboard: {e}")
            else:
                why.append("wl-clipboard não instalado")
        if not os.environ.get("DISPLAY"):
            why.append("sem X/XWayland para o xclip")
        elif not shutil.which("xclip"):
            why.append("xclip não instalado")
        else:
            if why:
                log.debug("usando xclip: %s", "; ".join(why))
            return _XClipboard()
        raise OSError("; ".join(why) + " (instale o pacote xclip, e também wl-clipboard no Wayland)")


# --------------------------------------------------------------------------

class ClipboardSync:
    """Observa a área de transferência local e aplica a que vem do outro PC.

    on_change(kind, data) é chamado quando o usuário copia algo aqui.
    """

    def __init__(self, on_change, interval=0.3):
        self.on_change = on_change
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last = None  # fingerprint do conteúdo atual (local ou recebido)
        self._backend = None
        self._quiet_until = 0.0  # logo após colar algo recebido, não lê (evita eco)
        self._pending = False

    def start(self):
        try:
            self._backend = _Backend()
        except Exception as e:
            log.warning("Área de transferência desativada: %s", e)
            return
        try:
            current = self._backend.read()
            self._last = fingerprint(*current) if current else None
        except Exception:
            pass
        threading.Thread(target=self._loop, name="clipboard", daemon=True).start()
        log.info("Área de transferência compartilhada (texto e imagens): %s", self._backend.name)

    def stop(self):
        self._stop.set()
        close = getattr(self._backend, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass

    def set(self, kind, data):
        """Conteúdo que chegou do outro computador."""
        if self._backend is None or kind not in ("text", "image") or not data:
            return
        fp = fingerprint(kind, data)
        with self._lock:
            if fp == self._last:
                return
            self._last = fp
            try:
                self._backend.write(kind, data)
                self._quiet_until = time.monotonic() + 1.0
                log.info("Recebido do outro PC: %s", _describe(kind, data))
            except Exception as e:
                log.warning("Não consegui colar o conteúdo recebido: %s", e)

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                if self._backend.changed():
                    self._pending = True
                if not self._pending or time.monotonic() < self._quiet_until:
                    continue
                self._pending = False
                time.sleep(0.05)  # deixa o outro programa terminar de escrever
                content = self._backend.read()
            except Exception as e:
                log.debug("falha lendo área de transferência: %s", e)
                continue
            if not content:
                continue
            kind, data = content
            if (kind == "text" and len(data) > MAX_TEXT) or (kind == "image" and len(data) > MAX_IMAGE):
                log.info("Conteúdo copiado grande demais para enviar (%s)", _describe(kind, data))
                continue
            fp = fingerprint(kind, data)
            with self._lock:
                if fp == self._last:
                    continue
                self._last = fp
            log.info("Copiado aqui, enviando: %s", _describe(kind, data))
            self.on_change(kind, data)


def _describe(kind, data):
    if kind == "text":
        return f"texto ({len(data)} caracteres)"
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return f"imagem {im.size[0]}x{im.size[1]} ({len(data) // 1024} KB)"
    except Exception:
        return f"imagem ({len(data) // 1024} KB)"
