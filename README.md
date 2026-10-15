# savevideo-bot

A production-ready Telegram bot that downloads **video**, **audio** and
**thumbnails** from YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit,
Vimeo, Pinterest, SoundCloud and 1000+ other sites (via
[yt-dlp](https://github.com/yt-dlp/yt-dlp)).

Send the bot a link → it shows the available qualities and sizes → pick one →
the file is downloaded, transcoded if needed and uploaded to the chat. Results
are cached by Telegram `file_id`, so repeat downloads are instant.

## Features

- 🎬 **Video** (up to the max available height), **audio** (MP3 / M4A) and
  **thumbnail** downloads.
- 🧮 **Real size estimates** per quality in the picker, built from the actual
  yt-dlp formats.
- 🪜 **Upload-limit resilience**: files larger than your Bot API limit are never
  split or re-encoded. The bot keeps the finished file and returns a signed,
  expiring **download link** (nginx `secure_link`); if links are not configured
  it reports a clear error. Oversized options are flagged in the picker
  beforehand.
- 📦 **Large files**: run a local Bot API server to send up to **2000 MB** as a
  single file (`BOT_API_URL`, see [Large files](#large-files-local-bot-api)).
- ⚡ **Instant cache**: a URL+format → `file_id` cache in SQLite (no re-download
  for repeated links).
- 🧵 **Concurrency queue** with `MAX_JOBS` workers and per-user queue positions.
- 🚦 **Per-user rate limiting**: cooldown, daily quota, one active job per user
  (admins are exempt).
- 🛡️ **SSRF guard**: private/reserved targets and redirect chains are rejected
  before a URL ever reaches yt-dlp.
- 📃 **Playlists** (`/playlist`) and **media groups** for carousel posts.
- 🔍 **Inline mode** (`@yourbot <url>`) with cached results and deep links.
- 🌍 **Localisation**: English, German, Farsi (fallback chain per user).
- 🛠️ **Admin tools**: `/stats`, `/broadcast`, `/ban`, `/unban`,
  `/update_ytdlp`, `/health`.
- ♻️ **Self-updating yt-dlp** (daily) plus automatic update-and-retry when an
  extractor breaks.
- 🐳 **Docker / Compose** and a **systemd** unit, plus an interactive
  `install.sh` wizard.

## Requirements

- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- **Docker path**: Docker + Docker Compose.
- **Bare-metal path**: Python 3.11+, `ffmpeg`, and a Linux host with systemd.

## Quick start (Docker)

```bash
git clone https://github.com/Tabasiarash/savevideo-bot.git
cd savevideo-bot
cp .env.example .env
$EDITOR .env            # set BOT_TOKEN (and ADMIN_IDS)
docker compose up -d --build
docker compose logs -f bot
```

Files up to **50 MB** work with the public Bot API. To allow **up to 2000 MB**,
see [Large files](#large-files-local-bot-api) below.

## One-click install on a VPS (systemd)

The interactive wizard collects your configuration and installs everything:

```bash
git clone https://github.com/Tabasiarash/savevideo-bot.git
cd savevideo-bot
sudo ./install.sh
```

It will:

1. ask for the **Bot API configuration** (bot token, admin IDs, upload limit,
   and optionally a local Bot API server with API ID/hash),
2. install system packages (`python3-venv`, `ffmpeg`) if missing,
3. create the service user, virtualenv and dependencies,
4. write `.env`, install and start the `savevideo-bot` systemd service.

Non-interactive variant (expects an existing `.env`):

```bash
sudo ./deploy.sh
```

Useful commands:

```bash
systemctl status savevideo-bot
journalctl -u savevideo-bot -f
sudo ./deploy.sh            # update code + deps and restart (idempotent)
```

## Configuration

All settings are environment variables, read from `.env` (see
[`.env.example`](.env.example)). **Only `BOT_TOKEN` is required** — every other
option has a safe default and the bot degrades gracefully.

| Variable | Default | Description |
| --- | --- | --- |
| `BOT_TOKEN` | — | **Required.** Token from @BotFather. |
| `ADMIN_IDS` | *(empty)* | Comma-separated numeric admin user IDs. |
| `MAX_UPLOAD_MB` | `50` / `2000` | Max upload size (MB). Auto-`2000` with `BOT_API_URL`; clamped to 2000. Legacy alias: `MAX_MB`. |
| `BOT_API_URL` | *(empty)* | Local Bot API base URL, e.g. `http://127.0.0.1:8081`. Legacy: `LOCAL_API`/`LOCAL_API_BASE`. |
| `BOT_API_DATA_DIR` | `/var/lib/telegram-bot-api` | Data volume of the local server (free space shown in `/health`). |
| `UPLOAD_TIMEOUT_SECONDS` | `3600` | Upload timeout; large files via a local server take a while. |
| `YT_MAX_CONCURRENCY` | `2` | Max concurrent yt-dlp downloads. |
| `DOWNLOAD_LINK_BASE_URL` | *(empty)* | Public origin serving `DOWNLOAD_LINK_DIR`. Links stay disabled until set. |
| `DOWNLOAD_LINK_SECRET` | *(empty)* | Shared secret; must match nginx `secure_link_md5`. |
| `DOWNLOAD_LINK_DIR` | `/srv/savevideo-links` | Finished over-limit files (same filesystem as `TEMP_DIR`). |
| `DOWNLOAD_LINK_TTL_HOURS` | `6` | Link validity. |
| `DOWNLOAD_LINK_MAX_ACTIVE` | `3` | Max active links per user. |
| `DOWNLOAD_LINK_MAX_TOTAL_GB` | `20` | Global cap for all stored links. |
| `MIN_FREE_DISK_GB` | `5` | Refuse a download when free space is below this. |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | *(empty)* | Credentials for the local server. |
| `MAX_JOBS` | `2` | Concurrent downloads. |
| `PER_USER_DAILY_LIMIT` | `30` | Downloads per user per UTC day. |
| `COOLDOWN_SECONDS` | `3` | Minimum seconds between requests. |
| `MAX_DURATION_MIN` | `60` | Reject media longer than this. |
| `INFO_TIMEOUT` | `30` | Metadata extraction timeout (s). |
| `DOWNLOAD_TIMEOUT` | `600` | Download timeout (s). |
| `CACHE_TTL_DAYS` | `30` | How long a `file_id` stays cached. |
| `AUTO_UPDATE_YTDLP` | `true` | Periodically `pip install -U yt-dlp`. |
| `UPDATE_CHECK_HOURS` | `24` | Update interval. |
| `COOKIES_FILE` | *(empty)* | Path to a Netscape `cookies.txt`. |
| `PROXY_URL` | *(empty)* | Proxy for yt-dlp, e.g. `socks5h://user:pass@host:1080`. |
| `PROXY_URL_INSTAGRAM` | *(empty)* | Per-platform override; falls back to `PROXY_URL`. |
| `PROXY_URL_TIKTOK` | *(empty)* | Per-platform override; falls back to `PROXY_URL`. |
| `PROXY_URL_X` | *(empty)* | Per-platform override; falls back to `PROXY_URL`. |
| `PROXY_URL_SOUNDCLOUD` | *(empty)* | Per-platform override; falls back to `PROXY_URL`. |
| `YTDLP_EXTRA_ARGS` | *(empty)* | Extra yt-dlp CLI args, shell-quoted. |
| `FORCE_IPV4` | `false` | Force IPv4 for every request. |
| `FORCE_IPV6` | `false` | Force IPv6, **YouTube URLs only**. |
| `YTDLP_JS_RUNTIME` | `auto` | JS engine for yt-dlp (`auto` = Deno→Node ≥22). Legacy alias: `JS_RUNTIME`. |
| `EGRESS_TEST_URL` | *(a YouTube video)* | URL probed by `/health`. |
| `DB_PATH` | `data/savevideo.db` | SQLite database path. |
| `TEMP_DIR` | `tmp` | Temporary download directory. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `DEFAULT_LANGUAGE` | `en` | Fallback UI language (`en`/`de`/`fa`). |

## Large files (local Bot API)

The public Bot API limits uploads to 50 MB. To raise this to 2000 MB, run a
[self-hosted Bot API server](https://github.com/tdlib/telegram-bot-api). Files
are always sent as a **single message** — the bot never splits them.

Anything still too large (or any upload that fails for a reason a link can fix)
is delivered as a **signed, expiring download link** instead, served by nginx.
See [deploy/download-links](deploy/download-links/README.md) for the nginx
`secure_link` setup (including the token formula the bot must agree on).

1. Get `api_id` / `api_hash` from <https://my.telegram.org>.
2. Set in `.env`:
   ```env
   BOT_API_URL=http://127.0.0.1:8081
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=abcdef0123456789
   MAX_UPLOAD_MB=2000
   ```
3. Start the server:
   - **Docker bot:** `docker compose --profile localapi up -d --build`
   - **systemd bot:** `cd deploy/telegram-bot-api && docker compose up -d`
     (see [deploy/telegram-bot-api](deploy/telegram-bot-api/README.md))

> **Shared directory:** in local mode the bot passes the absolute path of a
> finished download to the server, which opens the file itself. The bot's
> `TEMP_DIR` and the server must therefore share the **same path** (both
> compose files mount it for you).
>
> **First switch only:** log the bot out of the cloud API once by calling
> `https://api.telegram.org/bot<token>/logOut`. Cloud and local servers cannot
> be used at the same time.

See [docs/MIGRATION.md](docs/MIGRATION.md) for a step-by-step runbook (including
rollback) when migrating a running bot.

## YouTube: "Sign in to confirm you're not a bot"

Datacenter/VPS IPs are commonly walled by YouTube. The bot recognises the wall
and shows the `youtube_signin` hint instead of failing silently. To verify the
current setup, run `/health` in Telegram: its **Egress** line performs a live,
metadata-only yt-dlp call through the configured cookies/proxy and reports
`✅ <title>` or a failure class (`bot-wall`, `network`, `timeout`, `no-formats`).

### Works on a datacenter IP (free): cookies + JS runtime (built-in EJS)

Two ingredients clear the wall with no paid proxy:

1. **Fresh cookies** from a logged-in Google/YouTube session in `COOKIES_FILE`
   (`/etc/savevideo-bot/yt_cookies.txt` on the systemd host, mounted
   `./cookies/cookies.txt` in Docker). Export them from a browser with:
   ```bash
   # systemd host (needs the keyring / DBUS session that owns the Chrome profile)
   scripts/refresh_cookies.py --profile-dir ~/.config/google-chrome/Default \
       --output /etc/savevideo-bot/yt_cookies.txt
   ```
2. **JS runtime** — yt-dlp solves YouTube's `n`/signature challenge and mints the
   proof-of-origin (PO) token with its bundled **EJS** solver. EJS runs inside the
   yt-dlp process, so it honours `PROXY_URL` — no separate token server is
   needed. `YTDLP_JS_RUNTIME=auto` (default) prefers Deno, then Node **>= 22**
   (older Node is skipped automatically); the Docker image ships Deno. Pin with
   `YTDLP_JS_RUNTIME=node` or `node:/usr/bin/node`.

Cookies expire and automating an account carries some risk — refresh them when
`/health` regresses to `bot-wall`. A non-datacenter egress (below) clears the
wall without cookies.

### Alternative: a non-datacenter egress

1. **Home egress via Tailscale** (free). Tunnel the bot's yt-dlp traffic through
   a SOCKS5 server on a home machine — see
   [deploy/home-socks](deploy/home-socks/README.md) for a ready-made `ssh -D`
   systemd unit:
   ```env
   PROXY_URL=socks5h://127.0.0.1:1080
   ```
   (Use whatever local port is free — `1080` is often already taken; the
   home-socks example uses `1090`.)
   The bot's unit sets `RestrictAddressFamilies=AF_INET AF_INET6`, so
   `127.0.0.1` and `100.64.0.0/10` (Tailscale) are reachable as-is. Do **not**
   add `IPAddressDeny=any` unless you also allow-list every address the bot
   needs. Do **not** set a full exit node — only the bot's yt-dlp traffic should
   use this proxy. If the home link is metered (e.g. phone on mobile data),
   route only the sites that need it with the per-platform overrides:
   ```env
   PROXY_URL_INSTAGRAM=
   PROXY_URL_TIKTOK=
   PROXY_URL_X=
   ```
2. **Residential/mobile proxy** (paid). Must be *sticky* per job, because
   googlevideo media URLs are bound to the extraction IP:
   ```env
   PROXY_URL=http://user:pass@gate.example.com:8000
   ```

### Notes (tested on a real datacenter VPS)

- `FORCE_IPV6=true` is **not** a fix. Occasional successes are per-video
  anomalies (e.g. the Rickroll works on *any* IP). Applied to YouTube URLs only,
   never globally — a global IPv6 force breaks IPv4-only sites such as Vimeo.
- `--impersonate` (curl_cffi) and alternate `player_client`s do not defeat the
   wall on a flagged IP.
- Free tunnels (Cloudflare WARP) often **cannot** work on a VPS: the control
  plane connects but the QUIC data plane is blocked. Confirm with `/health`.
- `YTDLP_EXTRA_ARGS` adds extractor knobs without code changes, e.g.
  `--extractor-args "youtube:player_client=web_safari"`.

## SoundCloud and other audio-only sources

SoundCloud is supported **natively** by yt-dlp, so no extra configuration is
needed: send a track link, pick MP3 or M4A, and the bot downloads and uploads
the audio. Because a track has no video streams, the picker shows only the
audio (MP3/M4A), thumbnail and cancel buttons. A set/playlist works through
`/playlist <url>` like any other playlist (capped at `PLAYLIST_MAX_ITEMS`).
Route it through its own egress with the optional `PROXY_URL_SOUNDCLOUD`
override if needed.

## Admin commands

| Command | Description |
| --- | --- |
| `/stats` | Users, downloads, top sites, failures, cache hit-rate, disk. |
| `/broadcast <text>` | Send a message to every active user. |
| `/ban <user_id>` / `/unban <user_id>` | Block / unblock a user. |
| `/update_ytdlp` | Update yt-dlp immediately. |
| `/health` | Versions, disk, queue, proxy/cookies/JS-runtime/curl_cffi, a live egress probe, and the masked proxy exit IP. |

## How it works

```
Telegram ── aiogram 3 ──► handlers ──► JobQueue ──► YtDlpRunner (subprocess)
                                        │                 │
                                        │                 ▼
                                        ▼            yt-dlp download
                                   SQLite cache  ──► upload (video/audio)
                                        │                 │
                                        │                 ▼ too large / failed
                                        └────► signed download link (nginx)
```

- **Subprocess yt-dlp** (`python -m yt_dlp …`) so hung jobs can be killed on
  timeout and the event loop never blocks.
- **Progress** is parsed from yt-dlp's `[download] …%` lines to edit the status
  message.
- **SSRF**: URLs are checked against an IP-allowlist (`ipaddress.is_global`)
  and generic hosts have their redirect chain probed; known extractor sites are
  trusted to yt-dlp.
- **Cleanup**: each job gets its own temp dir; a startup sweep removes dirs
  older than an hour.

## Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # set BOT_TOKEN for manual runs

make run     # start the bot
make test    # pytest (mocked; no network)
make lint    # ruff
```

Optional real-network integration tests:

```bash
RUN_NETWORK_TESTS=1 pytest -m network
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| “Sign in to confirm you're not a bot” | Configure `COOKIES_FILE` or `PROXY_URL`. |
| Upload fails with a size error | Lower `MAX_UPLOAD_MB` or enable a local Bot API server. |
| `ffmpeg: not found` in `/health` | Install ffmpeg (`apt install ffmpeg`). |
| Downloads keep failing | `/update_ytdlp` or check `docker compose logs bot`. |
| Nothing works after switching to local API | Call `…/logOut` once (see above). |

## License

[MIT](LICENSE) © Arash Tabassi
