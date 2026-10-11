"""Gera dist/multidesk_<versão>_all.deb (Ubuntu, Debian, Mint, Pop!_OS...).

Roda em qualquer sistema (inclusive Windows): o .deb é montado em Python puro.
O pacote usa o Python do próprio Linux. As dependências que existem nos
repositórios (Xlib, cryptography, Tk, xclip) entram como Depends do apt, e as
que não existem (pynput, six, pyperclip, todas Python puro) vão junto no pacote.
"""

import importlib.util
import io
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import multidesk  # noqa: E402

VERSION = multidesk.__version__
PKG = "multidesk"
DEPENDS = "python3 (>= 3.8), python3-xlib, python3-cryptography, python3-tk, xclip"
VENDORED = ("pynput", "six", "pyperclip")
LIB = "usr/lib/multidesk"
MTIME = int(time.time())

CONTROL = f"""Package: {PKG}
Version: {VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: {DEPENDS}
Maintainer: GabrielAlvesB <GabrielAlvesB@users.noreply.github.com>
Description: Um mouse e um teclado para vários computadores
 Compartilhe mouse, teclado e área de transferência entre computadores
 Windows e Linux pela rede, levando o cursor até a borda da tela.
"""

# Libera o /dev/uinput para o usuário da sessão ativa (o mesmo que a Steam faz
# para controles). Sem isso só o root consegue criar teclado/mouse virtuais.
UDEV_RULE = """KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
"""

POSTINST = """#!/bin/sh
set -e
modprobe uinput 2>/dev/null || true
if command -v udevadm >/dev/null; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=change 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
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
            sys.exit(f"Instale {name} (pip install -r requirements.txt) antes de gerar o .deb")
        origin = Path(spec.origin)
        if origin.name == "__init__.py":
            yield from _source_files(origin.parent, f"{LIB}/vendor/{name}")
        else:
            yield origin, f"{LIB}/vendor/{origin.name}"


def _add(tar, name, data=None, mode=0o644, is_dir=False):
    info = tarfile.TarInfo("./" + name if name else ".")
    info.mtime = MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    if is_dir:
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
    else:
        info.size = len(data)
        info.mode = mode
        tar.addfile(info, io.BytesIO(data))


def _tar(files):
    """files: dict caminho -> (bytes, modo). Cria os diretórios intermediários."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        _add(tar, "", is_dir=True)
        seen = set()
        for name in sorted(files):
            parts = name.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d not in seen:
                    seen.add(d)
                    _add(tar, d, is_dir=True)
            data, mode = files[name]
            _add(tar, name, data, mode)
    return buf.getvalue()


def _ar(members):
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        out += f"{name:<16}{MTIME:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        out += data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def main():
    files = {}
    for src, dest in _source_files(ROOT / "multidesk", f"{LIB}/multidesk"):
        if src.is_file():
            files[dest] = (src.read_bytes(), 0o644)
    for src, dest in _vendored():
        if src.is_file():
            files[dest] = (src.read_bytes(), 0o644)
    files[f"{LIB}/run.py"] = (RUN_PY.encode(), 0o644)
    files["usr/bin/multidesk"] = (LAUNCHER.encode(), 0o755)
    files["usr/lib/udev/rules.d/70-multidesk-uinput.rules"] = (UDEV_RULE.encode(), 0o644)
    files["usr/lib/modules-load.d/multidesk.conf"] = (b"uinput\n", 0o644)
    files["usr/share/applications/multidesk.desktop"] = (DESKTOP.encode(), 0o644)
    files["usr/share/icons/hicolor/256x256/apps/multidesk.png"] = (
        (ROOT / "multidesk" / "assets" / "icon.png").read_bytes(), 0o644)

    installed_kb = sum(len(d) for d, _ in files.values()) // 1024 + 1
    control = CONTROL.replace("Section:", f"Installed-Size: {installed_kb}\nSection:", 1)
    control_tar = _tar({"control": (control.encode(), 0o644), "postinst": (POSTINST.encode(), 0o755)})

    out = ROOT / "dist" / f"{PKG}_{VERSION}_all.deb"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(_ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control_tar),
                         ("data.tar.gz", _tar(files))]))
    print(f"Pronto: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
