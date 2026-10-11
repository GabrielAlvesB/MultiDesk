"""Camada específica de cada sistema operacional.

Cada backend expõe:
  OS                  -- "win" ou "linux"
  init_process()      -- ajustes de processo (DPI no Windows, avisos no Linux)
  screen_bounds()     -- (x, y, largura, altura) da área de trabalho inteira
  Capture(sink)       -- captura mouse/teclado físicos (lado servidor)
  Injector()          -- simula mouse/teclado (lado cliente)

O `sink` da captura recebe:
  on_move(x, y)          movimento local (modo local)
  on_delta(dx, dy)       movimento relativo (modo remoto)
  on_button(nome, down)  nome em left/right/middle/x1/x2
  on_scroll(dx, dy)      em "cliques" de roda (pode ser fracionário)
  on_key(desc, down)     desc = {"k": nome} ou {"vk": int, "ch": str, "os": ...}
"""

import sys

if sys.platform == "win32":
    from .windows import OS, Capture, Injector, init_process, screen_bounds
elif sys.platform.startswith("linux"):
    from .linux import OS, Capture, Injector, init_process, screen_bounds
else:
    raise ImportError(f"Sistema não suportado: {sys.platform}")

__all__ = ["OS", "Capture", "Injector", "init_process", "screen_bounds"]
