#!/usr/bin/env sh
# Run LAN Chess without Docker (Linux / macOS / WSL).
# Creates a virtual environment on first use, loads .env and starts the server.
set -eu

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    "$PYTHON" -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -r requirements.txt
fi

if [ -f .env ]; then
    # Export every variable defined in .env
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

if ! command -v "${STOCKFISH_PATH:-stockfish}" >/dev/null 2>&1 \
    && [ ! -x /usr/games/stockfish ]; then
    echo "Note: Stockfish not found - play vs computer and analysis will be disabled."
    echo "      Install it (e.g. 'sudo apt install stockfish' / 'brew install stockfish')."
fi

exec .venv/bin/python -m app
