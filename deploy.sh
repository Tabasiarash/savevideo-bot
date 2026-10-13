#!/usr/bin/env bash
# savevideo-bot — idempotent systemd installer / updater.
#
# Usage (as root):
#   ./deploy.sh
# Overrides (env vars):
#   INSTALL_DIR=/opt/savevideo-bot  SERVICE_USER=savevideo  SERVICE_NAME=savevideo-bot
#   PYTHON=python3.12  SKIP_RESTART=1
#
# Re-running updates the code, dependencies and unit, then restarts the bot.
# The interactive configuration wizard lives in ./install.sh

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/savevideo-bot}"
SERVICE_USER="${SERVICE_USER:-savevideo}"
SERVICE_NAME="${SERVICE_NAME:-savevideo-bot}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "must run as root (use sudo)"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "$PYTHON_BIN not found"
command -v systemctl >/dev/null 2>&1 || die "systemd not available on this host"

if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg not found — installing via apt"
    apt-get update -qq && apt-get install -y -qq ffmpeg
fi

# --- service account -------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "creating system user '$SERVICE_USER'"
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER" \
        || useradd --system --home-dir "$INSTALL_DIR" --shell /sbin/nologin "$SERVICE_USER"
fi

# --- sync code -------------------------------------------------------------
log "installing application to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if [[ "$(readlink -f "$SRC")" != "$(readlink -f "$INSTALL_DIR")" ]]; then
    if git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$SRC" archive --format=tar HEAD | tar -x -C "$INSTALL_DIR"
    else
        tar -C "$SRC" \
            --exclude=.git --exclude=.venv --exclude=venv \
            --exclude=data --exclude=tmp --exclude=__pycache__ \
            --exclude=.pytest_cache --exclude=.ruff_cache --exclude='*.pyc' \
            -cf - . | tar -x -C "$INSTALL_DIR"
    fi
fi

mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/tmp"

# --- python environment ----------------------------------------------------
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    log "creating virtualenv"
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
fi
log "installing python dependencies"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel >/dev/null
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# --- permissions -----------------------------------------------------------
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data" "$INSTALL_DIR/tmp" "$INSTALL_DIR/.venv"
[[ -f "$INSTALL_DIR/.env" ]] && chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

# --- systemd unit ----------------------------------------------------------
log "installing systemd unit -> /etc/systemd/system/$SERVICE_NAME.service"
install -m 0644 "$INSTALL_DIR/savevideo-bot.service" "/etc/systemd/system/$SERVICE_NAME.service"
sed -i \
    -e "s|^User=.*|User=$SERVICE_USER|" \
    -e "s|^Group=.*|Group=$SERVICE_USER|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=$INSTALL_DIR/.env|" \
    -e "s|^ExecStart=.*|ExecStart=$INSTALL_DIR/.venv/bin/python -m bot.main|" \
    -e "s|^ReadWritePaths=.*|ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/tmp $INSTALL_DIR/.venv|" \
    "/etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    warn "no .env yet — run ./install.sh (wizard) or create $INSTALL_DIR/.env, then:"
    warn "  systemctl start $SERVICE_NAME"
    exit 0
fi

if [[ "${SKIP_RESTART:-0}" != "1" ]]; then
    log "restarting service"
    systemctl restart "$SERVICE_NAME"
    sleep 1
    systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
fi

log "done. logs: journalctl -u $SERVICE_NAME -f"
