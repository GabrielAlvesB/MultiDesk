"""Arquivos que o MultiDesk instala no Linux: os mesmos no .deb e no pacote do Arch.

O pacote usa o Python do próprio sistema. As dependências que existem nos
repositórios das distribuições (Xlib, cryptography, Tk, Pillow...) entram como
dependências do pacote, e as que não existem (pynput, customtkinter, pystray...,
todas Python puro) vão junto, em /usr/lib/multidesk/vendor.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import multidesk  # noqa: E402

VERSION = multidesk.__version__
PKG = "multidesk"
SUMMARY = "Um mouse e um teclado para vários computadores"
DESCRIPTION = ("Compartilhe mouse, teclado e área de transferência entre computadores\n"
               "Windows e Linux pela rede, levando o cursor até a borda da tela.")
MAINTAINER = "GabrielAlvesB <GabrielAlvesB@users.noreply.github.com>"
VENDORED = ("pynput", "six", "customtkinter", "darkdetect", "packaging", "pystray")
LIB = "usr/lib/multidesk"

# Libera o /dev/uinput para o usuário da sessão ativa (o mesmo que a Steam faz
# para controles). Sem isso só o root consegue criar teclado/mouse virtuais.
UDEV_RULE = """KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
"""

# Roda depois de instalar/atualizar (postinst do .deb, post_install do pacman).
SETUP_SH = """modprobe uinput 2>/dev/null || true
if command -v udevadm >/dev/null; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=change 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
"""

LAUNCHER = f"""#!/bin/sh
exec /usr/bin/python3 /{LIB}/run.py "$@"
"""

RUN_PY = f"""import sys
sys.path[:0] = ["/{LIB}", "/{LIB}/vendor"]
from multidesk.cli import main
main()
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=MultiDesk
Comment=Um mouse e um teclado para vários computadores
Exec=multidesk
Icon=multidesk
Terminal=false
Categories=Utility;Network;
Keywords=mouse;teclado;keyboard;kvm;deskflow;synergy;
StartupWMClass=Tk
"""


def _source_files(pkg_dir, dest):
    for path in sorted(pkg_dir.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        yield path, f"{dest}/{path.relative_to(pkg_dir).as_posix()}"


def _vendored():
    for name in VENDORED:
        spec = importlib.util.find_spec(name)
        if spec is None:
            sys.exit(f"Instale {name} (pip install -r requirements.txt) antes de gerar o pacote")
        origin = Path(spec.origin)
        if spec.submodule_search_locations and origin.name == "__init__.py":
            yield from _source_files(origin.parent, f"{LIB}/vendor/{name}")
        else:
            yield origin, f"{LIB}/vendor/{origin.name}"


def files():
    """dict caminho (sem / no início) -> (bytes, modo)."""
    out = {}
    for src, dest in _source_files(ROOT / "multidesk", f"{LIB}/multidesk"):
        if src.is_file():
            out[dest] = (src.read_bytes(), 0o644)
    for src, dest in _vendored():
        if src.is_file():
            out[dest] = (src.read_bytes(), 0o644)
    out[f"{LIB}/run.py"] = (RUN_PY.encode(), 0o644)
    out["usr/bin/multidesk"] = (LAUNCHER.encode(), 0o755)
    out["usr/lib/udev/rules.d/70-multidesk-uinput.rules"] = (UDEV_RULE.encode(), 0o644)
    out["usr/lib/modules-load.d/multidesk.conf"] = (b"uinput\n", 0o644)
    out["usr/share/applications/multidesk.desktop"] = (DESKTOP.encode(), 0o644)
    out["usr/share/icons/hicolor/256x256/apps/multidesk.png"] = (
        (ROOT / "multidesk" / "assets" / "icon.png").read_bytes(), 0o644)
    return out


def directories(paths):
    """Todos os diretórios intermediários dos caminhos, em ordem."""
    dirs = set()
    for name in paths:
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)
