"""Códigos de pareamento e links de conexão (multidesk://host:porta/CODIGO)."""

import secrets
import socket
from urllib.parse import urlparse

DEFAULT_PORT = 24850

# Sem I, L, O, 0, 1 para não confundir na hora de digitar.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code():
    """Gera um código tipo 'K7QM-2XPA-9RTD' (~59 bits de entropia)."""
    chars = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return "-".join(chars[i:i + 4] for i in range(0, 12, 4))


def normalize_code(code):
    return "".join(c for c in code.upper() if c.isalnum())


def local_ip():
    """IP desta máquina na rede local (o da interface que sai para a internet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_link(host, port, code):
    return f"multidesk://{host}:{port}/{code}"


def parse_link(text, code=None):
    """Aceita 'multidesk://host:porta/CODIGO', 'host:porta/CODIGO' ou só 'host'.

    Retorna (host, porta, codigo). O código pode vir separado em `code`.
    """
    text = text.strip()
    if "://" not in text:
        text = "multidesk://" + text
    url = urlparse(text)
    if not url.hostname:
        raise ValueError(f"Link inválido: {text!r}")
    found = url.path.strip("/") or code
    if not found:
        raise ValueError("O link não tem o código de pareamento.")
    return url.hostname, url.port or DEFAULT_PORT, found
