"""Gera dist/multidesk-<versão>-<rel>-any.pkg.tar.zst (Arch, CachyOS, EndeavourOS, Manjaro...).

Roda em qualquer sistema (inclusive Windows): o pacote do pacman é montado em
Python puro, com os mesmos arquivos do .deb (linux_payload.py). Instale com
`sudo pacman -U multidesk-*.pkg.tar.zst`: o pacman baixa as dependências.

Com --root DIR só copia os arquivos para DIR (usado pelo PKGBUILD, quando o
pacote é gerado no próprio Arch com makepkg).
"""

import argparse
import gzip
import hashlib
import io
import tarfile
import time
from pathlib import Path

from linux_payload import DESCRIPTION, MAINTAINER, PKG, ROOT, SUMMARY, VERSION, directories, files

PKGREL = 1
URL = "https://github.com/GabrielAlvesB/MultiDesk"
# Mesmos papéis do .deb. No Arch os opcionais não são instalados sozinhos,
# então a bandeja e o wl-clipboard entram como dependência de verdade.
# xorg-xwayland: a janela é Tk, que no Wayland roda pelo XWayland (KDE, GNOME
# e Hyprland já instalam; o Sway não).
DEPENDS = ("python>=3.10", "python-xlib", "python-cryptography", "tk", "python-pillow",
           "xclip", "wl-clipboard", "xorg-xwayland", "python-gobject", "libayatana-appindicator")
OPTDEPENDS = ("xwayland-satellite: abrir a janela do MultiDesk no niri",)
INSTALL = (ROOT / "packaging" / "arch" / "multidesk.install").read_bytes()  # o mesmo do PKGBUILD
MTIME = int(time.time())


def _pkginfo(size):
    lines = [
        "# Gerado por tools/build_arch.py",
        f"pkgname = {PKG}", f"pkgbase = {PKG}", "xdata = pkgtype=pkg",
        f"pkgver = {VERSION}-{PKGREL}",
        f"pkgdesc = {SUMMARY}: {DESCRIPTION.splitlines()[0]}",
        f"url = {URL}", f"builddate = {MTIME}", f"packager = {MAINTAINER}",
        f"size = {size}", "arch = any", "license = LicenseRef-custom",
    ]
    lines += [f"depend = {d}" for d in DEPENDS]
    lines += [f"optdepend = {d}" for d in OPTDEPENDS]
    return ("\n".join(lines) + "\n").encode()


def _mtree_path(name):
    # Mesmo escape do bsdtar: fora do ASCII visível vira \ooo (byte a byte).
    return "./" + "".join(chr(b) if 0x21 <= b <= 0x7E and b not in b"#\\" else f"\\{b:03o}"
                          for b in name.encode())


def _mtree(entries, dirs):
    """.MTREE: lista dos arquivos com hash, usada pelo `pacman -Qkk`."""
    lines = ["#mtree", "/set type=file uid=0 gid=0 mode=644"]
    for name in sorted(set(entries) | set(dirs)):
        if name in dirs:
            lines.append(f"{_mtree_path(name)} time={MTIME}.0 mode=755 type=dir")
            continue
        data, mode = entries[name]
        extra = f" mode={mode:o}" if mode != 0o644 else ""
        lines.append(f"{_mtree_path(name)} time={MTIME}.0{extra} size={len(data)} "
                     f"md5digest={hashlib.md5(data).hexdigest()} sha256digest={hashlib.sha256(data).hexdigest()}")
    return gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0)


def _add(tar, name, data=None, mode=0o644):
    info = tarfile.TarInfo(name)
    info.mtime = MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    if data is None:
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info)
    else:
        info.size = len(data)
        info.mode = mode
        tar.addfile(info, io.BytesIO(data))


def _compress(raw):
    try:
        from compression import zstd  # Python 3.14+
        return "zst", zstd.compress(raw, level=19)
    except ImportError:
        import lzma
        return "xz", lzma.compress(raw, preset=9)  # o pacman aceita .pkg.tar.xz também


def build():
    payload = files()
    dirs = directories(payload)
    meta = {".PKGINFO": (_pkginfo(sum(len(d) for d, _ in payload.values())), 0o644),
            ".INSTALL": (INSTALL, 0o644)}
    meta[".MTREE"] = (_mtree({**meta, **payload}, dirs), 0o644)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in (".PKGINFO", ".MTREE", ".INSTALL"):  # o pacman lê os metadados primeiro
            _add(tar, name, *meta[name])
        for d in dirs:
            _add(tar, d)
        for name in sorted(payload):
            _add(tar, name, *payload[name])
    ext, data = _compress(buf.getvalue())

    out = ROOT / "dist" / f"{PKG}-{VERSION}-{PKGREL}-any.pkg.tar.{ext}"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(data)
    print(f"Pronto: {out.relative_to(ROOT)}")


def install_to(root):
    for name, (data, mode) in files().items():
        dest = Path(root) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(mode)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", help="só copia os arquivos para este diretório (PKGBUILD)")
    args = p.parse_args()
    if args.root:
        install_to(args.root)
    else:
        build()


if __name__ == "__main__":
    main()
