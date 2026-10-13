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
- 🪜 **Upload-limit resilience**: if a file is too big for your Bot API the bot
  automatically tries a lower quality, then re-encodes with ffmpeg to fit, then
  splits audio into parts — and only then reports an error.
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
| `MAX_MB` | `50` | Max upload size (use `2000` with a local API server). |
| `LOCAL_API` | `false` | Use a self-hosted `telegram-bot-api` server. |
| `LOCAL_API_BASE` | `http://telegram-bot-api:8081` | Local server origin. |
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
| `PROXY_URL` | *(empty)* | Proxy for yt-dlp, e.g. `socks5://127.0.0.1:1080`. |
| `DB_PATH` | `data/savevideo.db` | SQLite database path. |
| `TEMP_DIR` | `tmp` | Temporary download directory. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `DEFAULT_LANGUAGE` | `en` | Fallback UI language (`en`/`de`/`fa`). |

## Large files (local Bot API)

The public Bot API limits uploads to 50 MB. To raise this to 2000 MB, run a
[self-hosted Bot API server](https://github.com/tdlib/telegram-bot-api):

1. Get `api_id` / `api_hash` from <https://my.telegram.org>.
2. Set in `.env`:
   ```env
   LOCAL_API=true
   LOCAL_API_BASE=http://telegram-bot-api:8081
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=abcdef0123456789
   MAX_MB=2000
   ```
3. `docker compose --profile localapi up -d --build`

> **First switch only:** log the bot out of the cloud API once by calling
> `https://api.telegram.org/bot<token>/logOut`. Cloud and local servers cannot
> be used at the same time.

## Cookies / proxy for YouTube

Many datacenter/VPS IPs get *“Sign in to confirm you're not a bot”* from
YouTube. Options, in order of preference:

1. **Cookies**: export a Netscape `cookies.txt` from a logged-in browser and
   set `COOKIES_FILE=/absolute/path/cookies.txt`. With Docker, drop the file at
   `./cookies/cookies.txt` and set `COOKIES_FILE=/cookies/cookies.txt`.
2. **Proxy**: route yt-dlp through a residential/SOCKS proxy via `PROXY_URL`.

The bot tells you when this is the problem (see `youtube_signin` in the
translations) instead of failing silently.

## Admin commands

| Command | Description |
| --- | --- |
| `/stats` | Users, downloads, top sites, failures, cache hit-rate, disk. |
| `/broadcast <text>` | Send a message to every active user. |
| `/ban <user_id>` / `/unban <user_id>` | Block / unblock a user. |
| `/update_ytdlp` | Update yt-dlp immediately. |
| `/health` | yt-dlp & ffmpeg versions, free disk, queue, API mode. |

## How it works

```
Telegram ── aiogram 3 ──► handlers ──► JobQueue ──► YtDlpRunner (subprocess)
                                        │                 │
                                        │                 ▼
                                        ▼            yt-dlp download
                                   SQLite cache  ──► ffmpeg re-encode/split
                                        │                 │
                                        └────► upload (video/audio) ──► cache file_id
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
| Upload fails with a size error | Lower `MAX_MB` or enable a local Bot API server. |
| `ffmpeg: not found` in `/health` | Install ffmpeg (`apt install ffmpeg`). |
| Downloads keep failing | `/update_ytdlp` or check `docker compose logs bot`. |
| Nothing works after switching to local API | Call `…/logOut` once (see above). |

## License

[MIT](LICENSE) © Arash Tabassi
