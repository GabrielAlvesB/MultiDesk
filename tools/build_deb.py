"""Gera dist/multidesk_<versão>_all.deb (Ubuntu, Debian, Mint, Pop!_OS...).

Roda em qualquer sistema (inclusive Windows): o .deb é montado em Python puro.
Os arquivos instalados vêm de linux_payload.py (os mesmos do pacote do Arch).
"""

import io
import tarfile
import time

from linux_payload import (DESCRIPTION, MAINTAINER, PKG, ROOT, SETUP_SH, SUMMARY, VERSION,
                           directories, files)

DEPENDS = ("python3 (>= 3.10), python3-xlib, python3-cryptography, python3-tk, "
           "python3-pil, python3-pil.imagetk, xclip")
# Ícone na bandeja: AppIndicator (Ubuntu já traz a extensão do GNOME ativada).
# wl-clipboard: área de transferência nos compositores Wayland que não são o GNOME.
RECOMMENDS = "python3-gi, gir1.2-ayatanaappindicator3-0.1, wl-clipboard"
MTIME = int(time.time())

CONTROL = f"""Package: {PKG}
Version: {VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: {DEPENDS}
Recommends: {RECOMMENDS}
Maintainer: {MAINTAINER}
Description: {SUMMARY}
""" + "".join(f" {line}\n" for line in DESCRIPTION.splitlines())

POSTINST = "#!/bin/sh\nset -e\n" + SETUP_SH + "exit 0\n"


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
        for d in directories(files):
            _add(tar, d, is_dir=True)
        for name in sorted(files):
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
    payload = files()
    installed_kb = sum(len(d) for d, _ in payload.values()) // 1024 + 1
    control = CONTROL.replace("Section:", f"Installed-Size: {installed_kb}\nSection:", 1)
    control_tar = _tar({"control": (control.encode(), 0o644), "postinst": (POSTINST.encode(), 0o755)})

    out = ROOT / "dist" / f"{PKG}_{VERSION}_all.deb"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(_ar([("debian-binary", b"2.0\n"), ("control.tar.gz", control_tar),
                         ("data.tar.gz", _tar(payload))]))
    print(f"Pronto: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
