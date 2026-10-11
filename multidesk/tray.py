"""Ícone na bandeja do sistema (Windows: perto do relógio; Linux: barra superior).

O pystray roda numa thread própria; os cliques do menu são repassados para a
janela por `post`, que coloca a ação na fila tratada pela thread do Tk.
"""

import logging
import shutil
import subprocess
import sys
import threading

log = logging.getLogger("multidesk.tray")


def _linux_tray_visible():
    """No Linux o ícone só aparece se o painel tiver uma bandeja (StatusNotifier).
    O GNOME sem a extensão AppIndicator aceita o ícone mas não mostra nada."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                              None, Gio.DBusCallFlags.NONE, 2000, None)
        return bool(reply.unpack()[0])
    except Exception:
        pass
    if shutil.which("gdbus"):
        try:
            out = subprocess.run(
                ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                 "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner",
                 "org.kde.StatusNotifierWatcher"], capture_output=True, text=True, timeout=3).stdout
            return "true" in out
        except Exception:
            pass
    return False

try:
    import pystray
    from PIL import Image
except Exception as e:  # pragma: no cover - depende do sistema
    pystray = None
    log.info("Bandeja do sistema indisponível: %s", e)


class Tray:
    def __init__(self, icon_path, post, status_text, running, actions):
        """post(fn): executa fn na thread da janela.
        status_text(): texto de status; running(): se está ativo.
        actions: dict com show, toggle, quit."""
        self.available = False
        self._icon = None
        if pystray is None:
            return
        self._post = post
        self._status_text = status_text
        self._running = running
        self._actions = actions
        try:
            image = Image.open(icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Abrir MultiDesk", self._do("show"), default=True),
                pystray.MenuItem(lambda _: self._status_text(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: "Parar" if self._running() else "Iniciar", self._do("toggle")),
                pystray.MenuItem("Sair", self._do("quit")),
            )
            self._icon = pystray.Icon("multidesk", image, "MultiDesk", menu)
            threading.Thread(target=self._run, name="tray", daemon=True).start()
            self.available = True
            if sys.platform.startswith("linux") and not _linux_tray_visible():
                self.available = False
                log.info("Painel sem bandeja do sistema: o ícone não vai aparecer; "
                         "fechar a janela com o MultiDesk ativo vai encerrá-lo.")
        except Exception as e:
            log.warning("Não consegui criar o ícone da bandeja: %s", e)

    def _run(self):
        try:
            self._icon.run()
        except Exception as e:
            self.available = False
            log.warning("Ícone da bandeja parou: %s", e)

    def _do(self, name):
        return lambda icon, item: self._post(self._actions[name])

    def update(self, tooltip):
        if not self._icon:
            return
        try:
            self._icon.title = tooltip[:120]
            self._icon.update_menu()
        except Exception:
            pass

    def notify(self, message):
        if self._icon:
            try:
                self._icon.notify(message, "MultiDesk")
            except Exception:
                pass

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
