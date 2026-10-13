#!/usr/bin/env bash
# Avvio senza installazione (usa il venv locale se presente)
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
cd "$DIR" && exec "$PY" -m cuestage "$@"
