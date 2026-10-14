# Migration runbook: splitting → single messages + download links

This runbook moves a running **savevideo-bot** install away from the old
behaviour (public Bot API + splitting oversized files into parts):

- oversized media is **never split or re-encoded**; it is either accepted whole
  (<= the Bot API limit) or delivered as a **signed, expiring download link**;
- uploads up to **2000 MB** via a self-hosted **local Bot API** server;
- hardened yt-dlp egress (proxy, retries, JS runtime, `/health` diagnostics).

The phases are independent: ship the download-link change first (it works even
behind the 50 MB cloud API), then move to a local Bot API server if you want
native >50 MB uploads. Work is done **locally** in small commits; this document
is the operational runbook.

## Prerequisites

- Root (or sudo) on the bot host, and Docker for the local Bot API server.
- `api_id` / `api_hash` from <https://my.telegram.org>.
- The bot token (in the existing `.env`).

## 0. Back up

```bash
cd /opt/savevideo-bot
sudo cp .env .env.bak.$(date +%F)
sudo cp data/savevideo.db data/savevideo.db.bak.$(date +%F) 2>/dev/null || true
sudo cp /etc/savevideo-bot/yt_cookies.txt /etc/savevideo-bot/yt_cookies.txt.bak 2>/dev/null || true
```

## 1. Update the code

```bash
sudo ./deploy.sh          # syncs code + deps, reinstalls unit, restarts
```

The new unit adds `RestrictAddressFamilies=AF_INET AF_INET6`. Leave the
`IPAddressAllow`/`IPAddressDeny` block commented unless you have listed every
address the bot needs.

## 2. Configure the local Bot API

```bash
cd /opt/savevideo-bot/deploy/telegram-bot-api
cp .env.example .env
sudo $EDITOR .env          # TELEGRAM_API_ID / TELEGRAM_API_HASH, BOT_TMP
docker compose up -d
docker compose logs -f telegram-bot-api
```

`BOT_TMP` must equal the bot's `TEMP_DIR`. In local mode the bot hands the
server an **absolute filesystem path** and the server opens the file itself, so
both processes must see the same directory at the same path.

## 3. Point the bot at the server

```bash
sudo $EDITOR /opt/savevideo-bot/.env
```

```env
BOT_API_URL=http://127.0.0.1:8081
MAX_UPLOAD_MB=2000
```

`MAX_UPLOAD_MB` is clamped to 2000. The legacy `LOCAL_API`/`LOCAL_API_BASE` and
`MAX_MB` names still work, so an old `.env` keeps starting — but prefer the new
names.

## Download links (optional, works without a local server)

Files over the upload limit become signed links instead of being split. This
needs nginx (or any server that can compute nginx's `secure_link` MD5 token) and
three settings:

```env
DOWNLOAD_LINK_BASE_URL=https://dl.example.com
DOWNLOAD_LINK_SECRET=<same value as nginx secure_link_md5>
DOWNLOAD_LINK_DIR=/srv/savevideo-links          # same filesystem as TEMP_DIR
```

See [../deploy/download-links/README.md](../deploy/download-links/README.md) for
the nginx `secure_link` config, the exact token formula, and a `curl` test. The
feature stays disabled until `DOWNLOAD_LINK_BASE_URL` and
`DOWNLOAD_LINK_SECRET` are set, so it is safe to leave blank.

## 4. Log the bot out of the cloud API (one time)

A bot cannot use the cloud and a local Bot API at the same time. Run this
**once**:

```bash
TOKEN=$(grep -E '^BOT_TOKEN=' /opt/savevideo-bot/.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOKEN}/logOut"
```

## 5. Restart and verify

```bash
sudo systemctl restart savevideo-bot
journalctl -u savevideo-bot -f
```

In Telegram, as an admin, run `/health`. Expect:

- `Mode: local`
- `Upload limit: 2000 MB`
- `Bot API: ok @yourbot`
- `Data dir: <free space>` (the server's data volume)
- `Egress: ✅ <title>`

Then send a link for a **>50 MB** video and confirm it arrives as a **single
file** (no `_part1`/`_part2`). Also confirm a small audio download still works.

## 6. Egress hardening (optional but recommended)

If YouTube shows `bot-wall` on `/health`, add one of:

- **Home egress** — see `deploy/home-socks/`, then set
  `PROXY_URL=socks5h://127.0.0.1:1080`.
- **Fresh cookies + PO-token provider + JS runtime** — see the README
  ("Works on a datacenter IP").

After changing these, restart and re-check `/health`. The egress probe result is
cached for 5 minutes.

## Rollback

```bash
sudo $EDITOR /opt/savevideo-bot/.env     # remove BOT_API_URL, set MAX_UPLOAD_MB=50
TOKEN=$(grep -E '^BOT_TOKEN=' /opt/savevideo-bot/.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOKEN}/logOut"   # switch back to cloud
sudo systemctl restart savevideo-bot
```

Then `cd deploy/telegram-bot-api && docker compose down`. The database and cache
are untouched by the migration, so cached files keep working.

## What changed (for reference)

| Area | Before | After |
| --- | --- | --- |
| Oversized media | split into parts | signed, expiring download link |
| Big-file uploads | 50 MB cap | up to 2000 MB via local Bot API (or a link) |
| Picker | — | every quality listed; over-limit options flagged for link delivery |
| Disk safety | none | refuse to start a download below `MIN_FREE_DISK_GB` |
| yt-dlp | 3 retries, no request sleep | 10 retries, `--sleep-requests 1`, concurrency cap |
| IP family | applied globally | skipped when a proxy is set |
| Format cap | `best` capped at 1080p | `best` = highest available |
| `/health` | basic | server mode, upload limit, Bot API reachability, data dir |
