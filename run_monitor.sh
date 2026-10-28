#!/data/data/com.termux/files/usr/bin/bash
# Ejecuta un ciclo del monitor usando el entorno virtual y las variables de .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MODO_BOT=0
MODO_VERIFY=0
for arg in "$@"; do
    case "$arg" in
        --bot) MODO_BOT=1 ;;
        --verify) MODO_VERIFY=1 ;;
    esac
done

LOCK_FILE="${DEPTO_LOCK_FILE:-$SCRIPT_DIR/.depto-monitor.lock}"
export DEPTO_LOCK_FILE="$LOCK_FILE"

LOCK_HELD=0
FLOCK_DISPONIBLE=0
if command -v flock >/dev/null 2>&1; then
    FLOCK_DISPONIBLE=1
    exec 9>>"$LOCK_FILE"
    if flock -n 9; then
        LOCK_HELD=1
    elif [ "$MODO_BOT" -eq 1 ] || [ "$MODO_VERIFY" -eq 1 ]; then
        echo "Aviso: hay un ciclo en ejecución; se omite git pull." >&2
    else
        echo "Aviso: ya hay un ciclo en ejecución; se omite este ciclo." >&2
        exit 0
    fi
else
    echo "Aviso: flock no disponible; el lock de ciclo lo maneja Python." >&2
fi

# La actualización nunca debe impedir que el monitor siga funcionando.
if [ "$LOCK_HELD" -eq 1 ] || [ "$FLOCK_DISPONIBLE" -eq 0 ]; then
    if ! GIT_TERMINAL_PROMPT=0 git pull --ff-only origin main; then
        echo "Aviso: no se pudo actualizar desde GitHub; se continúa con el código actual." >&2
    fi
fi

# El bot no debe retener el lock del ciclo: cron necesita ejecutar mientras escucha.
if [ "$MODO_BOT" -eq 1 ] || [ "$MODO_VERIFY" -eq 1 ]; then
    if [ "$FLOCK_DISPONIBLE" -eq 1 ]; then
        exec 9>&-
    fi
    LOCK_HELD=0
fi

export TZ="${TZ:-America/Santiago}"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# Solo el lock de este script puede marcar el ciclo como ya protegido.
unset DEPTO_LOCK_HELD

if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock >/dev/null 2>&1 || true
fi

if [ "$LOCK_HELD" -eq 1 ]; then
    export DEPTO_LOCK_HELD=1
fi

exec ./.venv/bin/python main.py "$@"
