# savevideo-bot

A production-ready Telegram bot that downloads **video**, **audio** and
**thumbnails** from YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit,
Vimeo, Pinterest, SoundCloud and 1000+ other sites (via
[yt-dlp](https://github.com/yt-dlp/yt-dlp)).

Send the bot a link → it shows the available qualities and sizes → pick one →
the file is downloaded, transcoded if needed and uploaded to the chat. Results
are cached by Telegram `file_id`, so repeat downloads are instant.

Spotify links are supported through **metadata-matching** (the bot finds a
downloadable equivalent from another source) rather than by downloading from
Spotify itself; see [Spotify](#spotify-metadata-match).

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
- 💬 **Subtitles**: pick a subtitle language (manual or auto/auto-translated) and
  deliver it **burned in** (visible in Telegram), as an embedded soft track, or
  as an `.srt` file. Default mode is configurable with `/settings`.
- ⚡ **Instant cache**: a URL+format → `file_id` cache in SQLite (no re-download
  for repeated links).
- 🧵 **Concurrency queue** with `MAX_JOBS` workers and per-user queue positions.
- 🚦 **Per-user rate limiting**: cooldown, daily quota, one active job per user
  (admins are exempt).
- 🛡️ **SSRF guard**: private/reserved targets and redirect chains are rejected
  before a URL ever reaches yt-dlp.
- 📃 **Playlists** (`/playlist`) and **media groups** for carousel posts.
- 🔍 **Inline mode** (`@yourbot <url>`) with cached results and deep links.
- 🌍 **Localisation & guide**: English, German, Farsi; welcome buttons, `/tutorial` guide, `/settings` for subtitle mode.
- 🛠️ **Admin tools**: `/stats`, `/users`, `/broadcast`, `/ban`, `/unban`,
  `/update_ytdlp`, `/cleanup`, `/health`.
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
| `MAX_DURATION_MIN` | `240` | Reject media longer than this (admins bypass). |
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
| `PROXY_URL_SPOTIFY` | *(empty)* | Per-platform override for Spotify metadata fetches. |
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

## Subtitles

When `ENABLE_SUBTITLES=true` (the default), picking a **video** quality first
shows a subtitle picker: manual tracks listed first, then the original-language
auto track `(auto)` and, when available, your language's translated track
` (auto-translated)`. Choosing "🚫 No subtitles" skips straight to download.

After picking a language the bot asks how to deliver it:

- **Burned in** (default) — subtitles are rendered into the picture, the only
  mode Telegram's player displays. Costs a re-encode (`libx264`). Videos longer
  than `SUBTITLE_BURN_MAX_MINUTES` (15) cannot be burned in full: the bot asks
  for the start of a 15-minute window (adjustable with ±5/±15-min buttons) and
  returns just that excerpt, burning the matching subtitle slice into it.
- **Embedded track** — a soft `mov_text` track (mp4). Playable in VLC etc., but
  **not shown by Telegram's player**.
- **`.srt` file**: the video is sent normally and the subtitle file is sent as a
  document alongside it.

`/settings` sets each user's default mode (burn / embed / srt). Playlists,
galleries and audio picks never offer subtitles. The fonts needed for correct
Farsi/Arabic shaping are installed by `install.sh`/`deploy.sh` (and the
Dockerfile). Relevant settings live in the `.env.example` "Subtitles" section.

## SoundCloud and other audio-only sources

SoundCloud is supported **natively** by yt-dlp, so no extra configuration is
needed: send a track link, pick MP3 or M4A, and the bot downloads and uploads
the audio. Because a track has no video streams, the picker shows only the
audio (MP3/M4A), thumbnail and cancel buttons. A set/playlist works through
`/playlist <url>` like any other playlist (capped at `PLAYLIST_MAX_ITEMS`).
Route it through its own egress with the optional `PROXY_URL_SOUNDCLOUD`
override if needed.

## Spotify (metadata-match)

Spotify streams are DRM-protected, so the bot **never downloads from Spotify and
never touches DRM**. Instead, when `ENABLE_SPOTIFY=true`, sending a Spotify
track link makes the bot read the track's metadata (oEmbed + page tags, no
credentials required), search the configured sources for a downloadable
equivalent, and offer the usual audio picker for the best match. The router is
always registered, so when the feature is off a Spotify link gets a clear
"disabled" reply instead of falling through to the generic link flow.

| Variable | Default | Description |
| --- | --- | --- |
| `ENABLE_SPOTIFY` | `false` | Turn the Spotify metadata-match flow on. |
| `SPOTIFY_MATCH_SOURCES` | `youtube,soundcloud` | Match sources, in priority order. |
| `SPOTIFY_MIN_SCORE` | `0.65` | Minimum score (0-1) to offer a candidate. |
| `SPOTIFY_DURATION_TOLERANCE_S` | `4` | Allowed length difference when scoring. |
| `SPOTIFY_ENABLE_COLLECTIONS` | `false` | Expand albums/playlists/shows/artists. |
| `SPOTIFY_MAX_ITEMS` | `25` | Cap on items per collection link. |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | *(empty)* | Optional Web API creds; required only for collections. |
| `SPOTIFY_META_CONCURRENCY` | `4` | Parallel metadata fetches for a collection. |
| `PROXY_URL_SPOTIFY` | *(empty)* | Per-platform override for metadata fetches. |

Collections (album/playlist/show/artist) are only enumerated when
`SPOTIFY_ENABLE_COLLECTIONS=true` **and** Web API credentials are set; each item
is matched and downloaded through the normal audio path. Matched downloads are
tagged with the track title/artist and cover art (MP3 ID3 / M4A attached
picture) via ffmpeg, best-effort.

## Podcast episodes (publisher feed)

A Spotify **episode** is not streamed from Spotify either. When
`ENABLE_PODCASTS=true` (off by default), the bot resolves the episode to the
publisher's own audio and sends that file as-is (tagged, never transcoded):

1. **iTunes Search API** — the publisher's `episodeUrl` (the same file a podcast
   app downloads), matched by title/show/duration.
2. **The show's RSS/Atom feed** — enclosures parsed with `defusedxml`.
3. **A YouTube/SoundCloud match** — only as a last resort (typically a
   Spotify-exclusive) and **clearly labelled** as a possibly-different copy.

The podcast lane is independent of `ENABLE_SPOTIFY`: episodes work with it off,
and a `SHOW` link gets a short "send an episode link" reply (single episodes are
supported for now). When the feature is off, an episode link still gets a clear
"disabled" message and never reaches the generic link flow.

Enclosures are fetched with the bot's own hardened HTTP client (not yt-dlp):
every redirect hop is validated against private/loopback/link-local addresses
and the connection is pinned to the checked address, the body must be audio,
and the size and total time are capped. No cookies are sent.

> **Privacy:** an episode's title and show name are sent to Apple's iTunes
> Search API to locate the publisher copy.

| Variable | Default | Description |
| --- | --- | --- |
| `ENABLE_PODCASTS` | `false` | Turn podcast-episode resolution on. |
| `PODCAST_MATCH_SOURCES` | `youtube,soundcloud` | Last-resort match sources. |
| `PODCAST_MIN_SCORE` | `0.65` | Minimum score (0-1) for a publisher match. |
| `PODCAST_EXCLUSIVE_MIN_SCORE` | `0.8` | Stricter bar for a last-resort match. |
| `PODCAST_DURATION_TOL_MIN_S` / `PODCAST_DURATION_TOL_PCT` | `30` / `0.03` | Percentage-based duration tolerance. |
| `PODCAST_MAX_DURATION_MIN` | `240` | Longest episode accepted. |
| `PODCAST_MAX_ENCLOSURE_MB` | `1024` | Hard cap on a single enclosure download. |
| `PODCAST_REGIONS` | `us,de` | iTunes storefronts to search, in order. |
| `PODCAST_MAX_FEED_BYTES` / `PODCAST_MAX_FEED_ITEMS` | `10485760` / `200` | Feed size/item caps. |
| `PODCAST_ITUNES_COOLDOWN_S` | `300` | Cooldown per storefront after an iTunes 403/429. |
| `PROXY_URL_PODCAST` | *(empty)* | Optional proxy for podcast HTTP (falls back to `PROXY_URL`). |

## Admin commands

| Command | Description |
| --- | --- |
| `/stats` | Users (total/24h/7d + new today/week), downloads, top sites, failures, cache hit-rate, disk. |
| `/users [limit]` | List the most recently seen users (id, @username, last seen, flags). |
| `/broadcast <text>` | Send a message to every active user. |
| `/ban <user_id>` / `/unban <user_id>` | Block / unblock a user. |
| `/update_ytdlp` | Update yt-dlp immediately. |
| `/cleanup [max_age_s]` | Delete unused temp dirs now (protects live flows and the download-link dir); reports freed bytes and free disk. `max_age_s` keeps entries newer than that. |
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
