#!/usr/bin/env bash
# Prepara o ambiente na primeira vez e abre o MultiDesk.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Criando ambiente Python..."
    python3 -m venv .venv
    .venv/bin/python -m pip install -q -r requirements.txt
fi
exec .venv/bin/python -m multidesk "$@"
