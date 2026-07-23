#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

cd "${PROJECT_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
    echo "[!] Липсва .venv. Създайте я и изпълнете:" >&2
    echo "    python3 -m venv .venv" >&2
    echo "    .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
fi

exec "${PYTHON}" main.py
