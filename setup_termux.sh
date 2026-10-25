#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  Instalación del monitor de departamentos en Termux (Android)
#  Uso: bash setup_termux.sh
# ============================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/mruanov/depto-monitor.git}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="${DEST:-$HOME/depto-monitor}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 */4 * * *}"

if [ ! -d /data/data/com.termux/files/usr ]; then
    echo "ERROR: este script debe ejecutarse dentro de Termux." >&2
    exit 1
fi

echo "==> Instalando paquetes de Termux..."
pkg update -y
pkg install -y git python cronie termux-services tzdata ca-certificates

if [ -d "$SCRIPT_DIR/.git" ]; then
    DEST="$SCRIPT_DIR"
    echo "==> Usando el repositorio existente en $DEST"
else
    if [ -d "$DEST/.git" ]; then
        echo "==> Actualizando repositorio en $DEST..."
        git -C "$DEST" pull --ff-only
    else
        echo "==> Clonando repositorio en $DEST..."
        git clone "$REPO_URL" "$DEST"
    fi
fi

cd "$DEST"

echo "==> Creando entorno virtual e instalando dependencias..."
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements-termux.txt

if [ ! -f .env ]; then
    echo "==> Configurando credenciales de Telegram..."
    cp .env.example .env
    read -r -p "TELEGRAM_BOT_TOKEN: " telegram_token
    read -r -p "TELEGRAM_CHAT_ID: " telegram_chat_id
    {
        printf 'TELEGRAM_BOT_TOKEN=%s\n' "$telegram_token"
        printf 'TELEGRAM_CHAT_ID=%s\n' "$telegram_chat_id"
    } > .env
    chmod 600 .env
fi

echo "==> Verificando conexión con Telegram..."
if ! ./run_monitor.sh --verify; then
    echo "ADVERTENCIA: no se pudo verificar Telegram. Revisa .env antes de continuar." >&2
fi

chmod +x run_monitor.sh setup_termux.sh

echo "==> Programando ejecución cada 4 horas..."
CRON_LOG="$DEST/monitor-cron.log"
CRON_LINE="$CRON_SCHEDULE $DEST/run_monitor.sh >> $CRON_LOG 2>&1"

tmp_cron="$(mktemp)"
if crontab -l 2>/dev/null | grep -vF "$DEST/run_monitor.sh" > "$tmp_cron"; then
    :
fi
printf '%s\n' "$CRON_LINE" >> "$tmp_cron"
crontab "$tmp_cron"
rm -f "$tmp_cron"

echo "==> Habilitando crond y wake-lock..."
termux-wake-lock >/dev/null 2>&1 || true
if ! sv-enable crond >/dev/null 2>&1; then
    echo "ADVERTENCIA: no se pudo habilitar crond con termux-services." >&2
    echo "             Ejecuta 'sv-enable crond' manualmente tras reiniciar Termux." >&2
fi
sv up crond >/dev/null 2>&1 || true

echo "==> Configurando arranque automático (requiere Termux:Boot)..."
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-depto-monitor.sh" <<BOOT
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock >/dev/null 2>&1 || true
sv up crond >/dev/null 2>&1 || true
"$DEST/run_monitor.sh" --bot >> "$DEST/telegram-bot.log" 2>&1 &
BOOT
chmod +x "$HOME/.termux/boot/start-depto-monitor.sh"

cat <<EOF

============================================================
 Instalación completada.
 Repositorio: $DEST
 Log del cron: $CRON_LOG
 Horario: $CRON_SCHEDULE (hora America/Santiago)

 Prueba manual:
   cd $DEST && ./run_monitor.sh --test
   cd $DEST && ./run_monitor.sh --verify
   cd $DEST && ./run_monitor.sh
   cd $DEST && ./run_monitor.sh --bot

 El chat de Telegram se inicia automáticamente con Termux:Boot.

 Revisa el log con:
   tail -f $CRON_LOG

 Recuerda desactivar la optimización de batería para Termux
 y, si usas Android TV, instalar la app Termux:Boot.
============================================================
EOF
