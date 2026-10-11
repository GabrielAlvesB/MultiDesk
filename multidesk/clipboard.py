"""Sincronização da área de transferência (texto) por polling."""

import logging
import threading

log = logging.getLogger("multidesk.clipboard")

MAX_TEXT = 1024 * 1024

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None


class ClipboardSync:
    def __init__(self, on_change, interval=0.7):
        self.on_change = on_change
        self.interval = interval
        self._last = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._warned = False

    def start(self):
        if pyperclip is None:
            log.warning("pyperclip não instalado: área de transferência desativada")
            return
        self._last = self._paste()
        threading.Thread(target=self._loop, name="clipboard", daemon=True).start()

    def stop(self):
        self._stop.set()

    def set(self, text):
        """Texto que chegou do outro computador."""
        if pyperclip is None or not isinstance(text, str):
            return
        with self._lock:
            self._last = text
            try:
                pyperclip.copy(text)
            except Exception as e:
                log.debug("falha ao escrever clipboard: %s", e)

    def _paste(self):
        try:
            return pyperclip.paste()
        except Exception as e:
            if not self._warned:
                self._warned = True
                log.warning("Não consegui ler a área de transferência (%s). "
                            "No Linux instale xclip: sudo apt install xclip", e)
            return None

    def _loop(self):
        while not self._stop.wait(self.interval):
            text = self._paste()
            with self._lock:
                if not text or text == self._last or len(text) > MAX_TEXT:
                    continue
                self._last = text
            self.on_change(text)
