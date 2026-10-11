"""Liga/desliga "iniciar com o sistema" (registro no Windows, ~/.config/autostart no Linux)."""

import shlex
import sys
from pathlib import Path

APP = "MultiDesk"


def _command():
    """Comando para abrir o app já iniciando o modo salvo."""
    if getattr(sys, "frozen", False):  # executável empacotado
        return [sys.executable, "--autostart"]
    if sys.platform.startswith("linux") and Path("/usr/bin/multidesk").exists():
        return ["/usr/bin/multidesk", "--autostart"]
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    return [exe, "-m", "multidesk", "--autostart"]


if sys.platform == "win32":
    import subprocess
    import winreg

    _KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def is_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
                winreg.QueryValueEx(k, APP)
                return True
        except OSError:
            return False

    def set_enabled(enabled):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, subprocess.list2cmdline(_command()))
            else:
                try:
                    winreg.DeleteValue(k, APP)
                except FileNotFoundError:
                    pass

else:
    _FILE = Path.home() / ".config" / "autostart" / "multidesk.desktop"

    def is_enabled():
        return _FILE.exists()

    def set_enabled(enabled):
        if not enabled:
            _FILE.unlink(missing_ok=True)
            return
        cmd = _command()
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(
            "[Desktop Entry]\nType=Application\nName=MultiDesk\n"
            f"Exec={shlex.join(cmd)}\nTryExec={cmd[0]}\nIcon=multidesk\n"
            "X-GNOME-Autostart-enabled=true\nNoDisplay=false\n",
            encoding="utf-8",
        )
