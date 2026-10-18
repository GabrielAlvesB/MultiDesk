import argparse
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path
import threading

from . import __version__
from .link import DEFAULT_PORT

SIDES = ("right", "left", "top", "bottom")

if sys.platform == "win32":
    LOG_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MultiDesk" / "multidesk.log"
else:
    LOG_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "multidesk" / "multidesk.log"


def _setup_logging(verbose):
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if sys.stderr is not None:  # no app instalado (sem console) não há stderr
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        root.addHandler(h)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        h.setFormatter(fmt)
        root.addHandler(h)
    except OSError:
        pass


def _wait_forever():
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(0.5):  # wait em pedaços para o Ctrl+C funcionar no Windows
        pass


def cmd_server(args):
    from .config import load_config
    from .server import Server
    layout = {getattr(args, s): s for s in SIDES if getattr(args, s)}
    server = Server(port=args.port, code=args.code or load_config()["code"], layout=layout, clipboard=not args.no_clipboard)
    server.start()
    print(f"\n  Link:   {server.link}\n  Código: {server.code}\n")
    try:
        _wait_forever()
    finally:
        server.stop()


def cmd_client(args):
    from .client import Client
    client = Client(args.link, name=args.name, side=args.side, clipboard=not args.no_clipboard)
    client.start()
    try:
        _wait_forever()
    finally:
        client.stop()


def cmd_gui(args):
    from .gui import main
    main(autostarted=args.autostart, resume=args.resume)


def main(argv=None):
    from . import platform
    platform.init_process()
    # Por padrão uma thread espera até 5 ms pela vez de rodar no Python, e cada
    # mensagem passa por várias threads: com o app ocupado (janela, área de
    # transferência) isso somava ~100 ms ao ping. Com 0,5 ms some.
    sys.setswitchinterval(0.0005)

    p = argparse.ArgumentParser(prog="multidesk",
                                description="Compartilhe mouse, teclado e área de transferência entre PCs.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    p.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)  # reaberto após atualizar
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("server", help="este PC tem o mouse/teclado e controla os outros")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--code", help="código de pareamento (padrão: o salvo em ~/.multidesk.json)")
    for side, pt in zip(SIDES, ("direita", "esquerda", "acima", "abaixo")):
        s.add_argument(f"--{side}", metavar="NOME", help=f"cliente que fica à {pt} desta tela")
    s.add_argument("--no-clipboard", action="store_true", help="não sincronizar área de transferência")
    s.set_defaults(func=cmd_server)

    c = sub.add_parser("client", help="este PC é controlado pelo servidor")
    c.add_argument("link", help="link do servidor, ex.: multidesk://192.168.0.10:24850/ABCD-EFGH-JKLM")
    c.add_argument("--name", help="nome deste PC (padrão: nome do computador)")
    c.add_argument("--side", choices=SIDES, help="onde esta tela fica em relação ao servidor")
    c.add_argument("--no-clipboard", action="store_true", help="não sincronizar área de transferência")
    c.set_defaults(func=cmd_client)

    g = sub.add_parser("gui", help="abre a janela (padrão)")
    g.set_defaults(func=cmd_gui)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    getattr(args, "func", cmd_gui)(args)
