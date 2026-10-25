#!/data/data/com.termux/files/usr/bin/bash
# Ejecuta un ciclo del monitor usando el entorno virtual y las variables de .env.
set -euo pipefail

cd "$(dirname "$0")"

git pull --ff-only origin main

export TZ="${TZ:-America/Santiago}"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock >/dev/null 2>&1 || true
fi

exec ./.venv/bin/python main.py "$@"
