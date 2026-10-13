#!/usr/bin/env bash
# =============================================================================
# savevideo-bot — one-click interactive installer for a fresh VPS.
#
# Usage:
#   sudo ./install.sh
#
# The wizard asks for your Bot API configuration, writes .env and then installs
# the systemd service via ./deploy.sh. It is safe to re-run.
#
# For unattended installs, pre-set the variables (e.g. BOT_TOKEN=... ) and run
# with a non-interactive stdin, or pass --yes.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- pretty output ---------------------------------------------------------
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$GREEN" "$RESET" "$BOLD" "$*" "$RESET"; }
info() { printf '%s[i]%s %s\n' "$BLUE" "$RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW" "$RESET" "$*"; }
err()  { printf '%s[x]%s %s\n' "$RED" "$RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- flags -----------------------------------------------------------------
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) die "unknown argument: $arg" ;;
    esac
done

# ---- root ------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        info "re-running with sudo…"
        exec sudo -E bash "$0" "$@"
    fi
    die "must run as root (use sudo)"
fi

INTERACTIVE=1
[[ -t 0 && $ASSUME_YES -eq 0 ]] || INTERACTIVE=0

# prompt "question" "default" -> echoes the answer
ask() {
    local question="$1" default="${2:-}" answer=""
    if [[ $INTERACTIVE -eq 0 ]]; then
        printf '%s' "$default"
        return
    fi
    if [[ -n "$default" ]]; then
        read -r -p "$(printf '%s%s%s [%s]: ' "$BOLD" "$question" "$RESET" "$default")" answer || true
        printf '%s' "${answer:-$default}"
    else
        read -r -p "$(printf '%s%s%s: ' "$BOLD" "$question" "$RESET")" answer || true
        printf '%s' "$answer"
    fi
}

confirm() {
    local question="$1" default="${2:-y}" answer=""
    [[ $INTERACTIVE -eq 0 ]] && { [[ "$default" == "y" ]]; return; }
    read -r -p "$(printf '%s%s%s [%s/%s]: ' "$BOLD" "$question" "$RESET" "${default/y/Y}" "${default/n/N}")" answer || true
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

validate_token() { [[ "$1" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{20,}$ ]]; }

# ---- base packages ---------------------------------------------------------
step "Installing base packages"
PKGS=()
install_pkgs() {
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq "$@"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q "$@"
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q "$@"
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache "$@"
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm "$@"
    else
        warn "unknown package manager — install these manually: $*"
        return 1
    fi
}

need() { command -v "$1" >/dev/null 2>&1; }

if need apt-get; then
    install_pkgs ca-certificates git ffmpeg python3 python3-venv python3-pip || true
elif need dnf; then
    install_pkgs ca-certificates git ffmpeg python3 python3-pip || true
elif need yum; then
    install_pkgs ca-certificates git ffmpeg python3 python3-pip || true
elif need apk; then
    install_pkgs ca-certificates git ffmpeg python3 py3-pip || true
elif need pacman; then
    install_pkgs ca-certificates git ffmpeg python python-pip || true
fi

# pick the best python
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done
[[ -n "$PYTHON_BIN" ]] || die "no python3 interpreter found"
info "using interpreter: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

"$PYTHON_BIN" -c 'import venv' 2>/dev/null || warn "python venv module missing — install python3-venv"
need ffmpeg || warn "ffmpeg not found — media re-encoding/splitting will be unavailable"

# ---- existing config -------------------------------------------------------
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    warn "an existing .env was found at $ENV_FILE"
    if confirm "Keep it and just (re)install the service?" y; then
        step "Re-running deployment with the existing configuration"
        PYTHON_BIN="$PYTHON_BIN" "$SCRIPT_DIR/deploy.sh"
        exit $?
    fi
    backup="$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
    cp "$ENV_FILE" "$backup"
    info "backed up existing .env to $backup"
fi

# ---- wizard ----------------------------------------------------------------
step "Bot API configuration"
say "${DIM}Tip: create a bot with @BotFather and copy its token.${RESET}"

BOT_TOKEN="${BOT_TOKEN:-}"
while ! validate_token "$BOT_TOKEN"; do
    BOT_TOKEN="$(ask "Telegram BOT_TOKEN" "$BOT_TOKEN")"
    validate_token "$BOT_TOKEN" || warn "that does not look like a bot token (expected 123456:ABC-DEF…)"
    [[ $INTERACTIVE -eq 0 ]] && break
done
validate_token "$BOT_TOKEN" || die "a valid BOT_TOKEN is required"

ADMIN_IDS="${ADMIN_IDS:-$(ask "Admin user IDs (comma-separated, optional)" "")}"
DEFAULT_LANGUAGE="$(ask "Default language (en/de/fa)" "${DEFAULT_LANGUAGE:-en}")"

step "Upload size & Bot API server"
say "  1) Public Bot API        — uploads up to 50 MB  ${DIM}(simplest)${RESET}"
say "  2) Local Bot API server  — uploads up to 2000 MB ${DIM}(needs api_id/api_hash)${RESET}"
CHOICE="$(ask "Choose 1 or 2" "1")"

LOCAL_API="false"
MAX_MB="50"
TELEGRAM_API_ID=""
TELEGRAM_API_HASH=""
LOCAL_API_BASE="http://127.0.0.1:8081"

if [[ "$CHOICE" == "2" ]]; then
    LOCAL_API="true"
    MAX_MB="2000"
    say ""
    info "Get api_id/api_hash from https://my.telegram.org (API development tools)."
    TELEGRAM_API_ID="$(ask "TELEGRAM_API_ID")"
    TELEGRAM_API_HASH="$(ask "TELEGRAM_API_HASH")"
    [[ -n "$TELEGRAM_API_ID" && -n "$TELEGRAM_API_HASH" ]] || die "api_id and api_hash are required for a local server"
    LOCAL_API_BASE="$(ask "Local Bot API base URL" "$LOCAL_API_BASE")"

    if need docker; then
        if confirm "Start a telegram-bot-api container now (published on 127.0.0.1:8081)?" y; then
            info "starting aiogram/telegram-bot-api container"
            docker rm -f telegram-bot-api >/dev/null 2>&1 || true
            docker run -d --name telegram-bot-api --restart unless-stopped \
                -p 127.0.0.1:8081:8081 \
                -e TELEGRAM_API_ID="$TELEGRAM_API_ID" \
                -e TELEGRAM_API_HASH="$TELEGRAM_API_HASH" \
                -e TELEGRAM_LOCAL=1 \
                -v telegram-bot-api-data:/var/lib/telegram-bot-api \
                aiogram/telegram-bot-api:latest >/dev/null
            info "container started; remember to call /logOut on the cloud API once"
        fi
    else
        warn "Docker not found. Run a telegram-bot-api server yourself and set LOCAL_API_BASE accordingly."
        warn "Example: docker run -d -p 127.0.0.1:8081:8081 -e TELEGRAM_API_ID=.. -e TELEGRAM_API_HASH=.. -e TELEGRAM_LOCAL=1 aiogram/telegram-bot-api"
    fi
else
    MAX_MB="$(ask "Max upload size in MB" "50")"
fi

step "Optional: anti-blocking"
COOKIES_FILE="$(ask "Path to cookies.txt (leave empty to skip)" "${COOKIES_FILE:-}")"
PROXY_URL="$(ask "Proxy URL for yt-dlp (leave empty to skip)" "${PROXY_URL:-}")"
if [[ -n "$COOKIES_FILE" && ! -f "$COOKIES_FILE" ]]; then
    warn "cookies file '$COOKIES_FILE' does not exist yet — the bot will ignore it until you create it"
fi

# ---- summary ---------------------------------------------------------------
step "Configuration summary"
printf '  %sBot token%s      : %s\n' "$DIM" "$RESET" "${BOT_TOKEN%%:*}:••••••••"
printf '  %sAdmins%s         : %s\n' "$DIM" "$RESET" "${ADMIN_IDS:-<none>}"
printf '  %sUpload limit%s   : %s MB\n' "$DIM" "$RESET" "$MAX_MB"
printf '  %sBot API%s        : %s\n' "$DIM" "$RESET" "$([[ "$LOCAL_API" == "true" ]] && echo "local ($LOCAL_API_BASE)" || echo cloud)"
printf '  %sLanguage%s       : %s\n' "$DIM" "$RESET" "$DEFAULT_LANGUAGE"
printf '  %sCookies%s        : %s\n' "$DIM" "$RESET" "${COOKIES_FILE:-<none>}"
printf '  %sProxy%s          : %s\n' "$DIM" "$RESET" "${PROXY_URL:-<none>}"

if ! confirm "Write .env and install the service?" y; then
    die "aborted by user"
fi

# ---- write .env ------------------------------------------------------------
step "Writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
# Generated by install.sh on $(date -u '+%Y-%m-%d %H:%M:%S UTC')
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS

MAX_MB=$MAX_MB

LOCAL_API=$LOCAL_API
LOCAL_API_BASE=$LOCAL_API_BASE
TELEGRAM_API_ID=$TELEGRAM_API_ID
TELEGRAM_API_HASH=$TELEGRAM_API_HASH

MAX_JOBS=2
PER_USER_DAILY_LIMIT=30
COOLDOWN_SECONDS=3
MAX_DURATION_MIN=60

INFO_TIMEOUT=30
DOWNLOAD_TIMEOUT=600

CACHE_TTL_DAYS=30

AUTO_UPDATE_YTDLP=true
UPDATE_CHECK_HOURS=24

COOKIES_FILE=$COOKIES_FILE
PROXY_URL=$PROXY_URL

DB_PATH=data/savevideo.db
TEMP_DIR=tmp
LOG_LEVEL=INFO

DEFAULT_LANGUAGE=$DEFAULT_LANGUAGE
EOF
chmod 600 "$ENV_FILE"
info ".env written (mode 600)"

# ---- deploy ----------------------------------------------------------------
step "Installing the application and systemd service"
PYTHON_BIN="$PYTHON_BIN" "$SCRIPT_DIR/deploy.sh"

cat <<EOF

${GREEN}${BOLD}Installation complete!${RESET}

  Status : systemctl status savevideo-bot
  Logs   : journalctl -u savevideo-bot -f
  Update : sudo ./deploy.sh
  Config : ${ENV_FILE}

Open your bot in Telegram and send /start.
EOF
