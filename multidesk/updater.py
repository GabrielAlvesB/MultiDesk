"""Atualização pelas Releases do GitHub: verifica, baixa, confere e instala.

Os nomes dos arquivos da release são fixos (o atualizador depende deles):
  MultiDesk-Setup-<v>.exe, multidesk_<v>_all.deb, multidesk-<v>-1-any.pkg.tar.zst
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger("multidesk.update")

REPO = "GabrielAlvesB/MultiDesk"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
LINUX_LIB = Path("/usr/lib/multidesk")
TIMEOUT = 15

# Só para testes: finge que esta é uma versão antiga.
CURRENT = os.environ.get("MULTIDESK_UPDATE_TEST_VERSION") or __version__


class UpdateError(Exception):
    pass


@dataclass
class Update:
    version: str
    notes: str
    page: str
    asset: str = None  # None: não dá para instalar sozinho (rodando do código-fonte)
    url: str = None
    size: int = 0
    sha256: str = None


def parse_version(text):
    parts = []
    for p in text.strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def install_kind():
    """'windows', 'pacman', 'deb' ou None (sem como se atualizar sozinho)."""
    if sys.platform == "win32":
        return "windows" if getattr(sys, "frozen", False) else None
    if sys.platform.startswith("linux") and Path(__file__).resolve().parent.parent == LINUX_LIB:
        if shutil.which("pacman"):
            return "pacman"
        if shutil.which("dpkg"):
            return "deb"
    return None


def asset_name(kind, version):
    return {"windows": f"MultiDesk-Setup-{version}.exe",
            "deb": f"multidesk_{version}_all.deb",
            "pacman": f"multidesk-{version}-1-any.pkg.tar.zst"}.get(kind)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"MultiDesk/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def check():
    """Retorna um Update se o GitHub tem versão mais nova, senão None."""
    with _get(API) as r:
        rel = json.load(r)
    version = rel.get("tag_name", "").lstrip("vV")
    if not version or parse_version(version) <= parse_version(CURRENT):
        return None
    up = Update(version=version, notes=(rel.get("body") or "").strip(), page=rel.get("html_url") or RELEASES_PAGE)
    name = asset_name(install_kind(), version)
    for a in rel.get("assets", []):
        if a.get("name") == name:
            digest = a.get("digest") or ""
            up.asset, up.url, up.size = name, a["browser_download_url"], int(a.get("size") or 0)
            up.sha256 = digest[7:].lower() if digest.startswith("sha256:") else None
    return up


def download(up, progress=None, cancelled=lambda: False):
    """Baixa o arquivo da atualização e confere tamanho e SHA-256. Retorna o caminho."""
    folder = Path(tempfile.mkdtemp(prefix="multidesk-update-"))
    folder.chmod(0o755)  # o apt lê o .deb como outro usuário
    path = folder / up.asset
    h = hashlib.sha256()
    done = 0
    try:
        with _get(up.url) as r, open(path, "wb") as f:
            total = up.size or int(r.headers.get("Content-Length") or 0)
            while True:
                if cancelled():
                    raise UpdateError("Download cancelado.")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        if up.size and done != up.size:
            raise UpdateError("O download veio incompleto. Tente de novo.")
        if up.sha256 and h.hexdigest() != up.sha256:
            raise UpdateError("O arquivo baixado não confere com o publicado no GitHub.")
        path.chmod(0o644)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    log.info("Atualização %s baixada (%d KB)", up.version, done // 1024)
    return path


def apply(path):
    """Instala o arquivo baixado.

    Windows: abre o instalador em modo silencioso, que fecha este processo e
    reabre o app no final; quem chama deve encerrar logo em seguida.
    Linux: instala com pkexec (pede a senha numa janela) e espera terminar;
    quem chama reinicia o app depois.
    """
    kind = install_kind()
    if kind == "windows":
        subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
        return
    if kind == "pacman":
        cmd = ["pacman", "-U", "--noconfirm", str(path)]
    elif kind == "deb":
        cmd = ["apt-get", "install", "-y", str(path)]
    else:
        raise UpdateError("Esta instalação não se atualiza sozinha.")
    if not shutil.which("pkexec"):
        raise UpdateError("Não encontrei o pkexec para pedir a senha. Instale pelo terminal:\n\n"
                          f"sudo {' '.join(cmd)}")
    r = subprocess.run(["pkexec", *cmd], capture_output=True, text=True)
    if r.returncode in (126, 127):
        raise UpdateError("A senha não foi confirmada; nada foi alterado.")
    if r.returncode != 0:
        log.error("instalação falhou:\n%s", (r.stdout + r.stderr).strip())
        raise UpdateError("A instalação falhou (detalhes no log). Pelo terminal:\n\n"
                          f"sudo {' '.join(cmd)}")
    log.info("Atualização instalada")


def restart_command():
    """Comando para reabrir o app depois de atualizado (Linux)."""
    return ["/usr/bin/multidesk", "--resume"]
