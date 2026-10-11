"""Configuração salva em ~/.multidesk.json (código do servidor, último link etc.)."""

import json
from pathlib import Path

from .link import new_code

CONFIG = Path.home() / ".multidesk.json"


def load_config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    # Código fixo entre execuções: o link dos clientes continua valendo.
    if "code" not in cfg:
        cfg["code"] = new_code()
        save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass
