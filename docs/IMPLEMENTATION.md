# savevideo-bot — Implementation Report

A reviewer-oriented description of how the bot is built, how a request flows,
how egress/anti-blocking and over-limit delivery work, and how to operate it.
It reflects the code at the commit that added this file; when the code and this
document disagree, the code wins.

- Language/runtime: Python 3.11+ (3.12 on the reference host), asyncio.
- Telegram framework: aiogram 3.
- Media engine: yt-dlp (CLI subprocess) + ffmpeg.
- Persistence: SQLite via aiosqlite.
- Entry point: `python -m bot.main`.

---

## 1. Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │                 Telegram                     │
                    │  update: message / callback / inline_query   │
                    └───────────────────────┬─────────────────────┘
                                            │ long polling (aiogram)
                                            ▼
                    ┌─────────────────────────────────────────────┐
                    │               Dispatcher (aiogram)           │
                    │  start → admin → links → inline (router order)│
                    └───────────────────────┬─────────────────────┘
                                            │
        ┌───────────────────────────────────┼───────────────────────────────────┐
        ▼                                   ▼                                   ▼
  handlers/start.py              handlers/links.py                     handlers/inline.py
  /start /help /language         URL → info → picker → download                @bot <url>
  deep links                     → upload / cache / link                     cached or deep link
                                            │
                                            ▼
                    ┌─────────────────────────────────────────────┐
                    │                 JobQueue                     │  MAX_JOBS workers
                    │   FIFO concurrency + queue-position report   │
                    └───────────────────────┬─────────────────────┘
                                            ▼
                    ┌─────────────────────────────────────────────┐
                    │              YtDlpRunner                     │
                    │  python -m yt_dlp … (killable subprocess)    │
                    │  cookies + proxy + JS runtime (EJS)          │
                    └───────┬───────────────────────┬─────────────┘
                            ▼                       ▼
                   SQLite cache              produced media file(s)
                   (url+fmt → file_id)              │
                            │                       ▼
                            │            fits upload limit?
                            │             ├─ yes → bot.send_video / send_audio
                            └────────────►└─ no  → DownloadLinkStore → nginx /dl/
```

Services are created once at startup and exposed through a container
(`bot/deps.py`, `Services`). Handlers reach them with `get_services()`; tests
replace an individual service with a fake.

---

## 2. Module map

| Area | File | Responsibility |
| --- | --- | --- |
| Config | `bot/config.py` | `Settings` (pydantic-settings); all env vars, derived helpers, upload-limit logic. |
| Entry | `bot/main.py` | Wires services, builds `Bot`/`Dispatcher`, startup sweep, maintenance + auto-update loops. |
| DI | `bot/deps.py` | `Services` container, `init_services`/`get_services`/`reset_services`. |
| Storage | `bot/db.py` | SQLite schema + versioned migrations, all queries (async). |
| Pure helpers | `bot/utils.py` | URL extraction/normalisation, host/site classification, SSRF checks, formatting. |
| i18n | `bot/i18n.py` | en/de/fa string tables (key sets asserted equal), fallback chain. |
| Handlers | `bot/handlers/start.py` | `/start`, `/help`, `/language`, deep-link entry. |
| | `bot/handlers/admin.py` | `/stats`, `/users`, `/broadcast`, `/ban`, `/unban`, `/update_ytdlp`, `/cleanup`, `/health`. |
| | `bot/handlers/links.py` | Core flow: detect → info → picker → download → upload/cache/link. |
| | `bot/handlers/spotify.py` | Spotify link detection → metadata → source match → audio picker; podcast-episode lane (both feature-gated). |
| | `bot/handlers/inline.py` | Inline mode: cached result or deep-link article. |
| | `bot/handlers/common.py` | Shared: `register_user`, language resolution, `text()`. |
| Services | `bot/services/downloader.py` | `YtDlpRunner`: yt-dlp args, cookies, proxy, JS runtime, download, probes, format/quality builder. |
| | `bot/services/download_links.py` | Signed-link store + `secure_link` token formula. |
| | `bot/services/queue.py` | `JobQueue` (FIFO concurrency limiter). |
| | `bot/services/ratelimit.py` | Cooldown, daily quota, one-active-job-per-user. |
| | `bot/services/cache.py` | URL+format → Telegram `file_id` cache. |
| | `bot/services/updater.py` | yt-dlp version check / self-update / update-and-retry heuristic. |
| | `bot/services/breaker.py` | Generic per-key failure breaker (threshold + cooldown, injectable clock). |
| | `bot/services/tagger.py` | Best-effort ffmpeg tagger (MP3 ID3 / M4A attached picture; arg list, no shell). |
| | `bot/services/spotify/urls.py` | Spotify URL parse/classify + short-link resolution. |
| | `bot/services/spotify/http.py` | Blocking curl_cffi GET/POST wrapped for asyncio, byte/redirect limits. |
| | `bot/services/spotify/metadata.py` | `TrackMeta` via oEmbed + page tags (+ optional Web API), TTL-cached; collection listing. |
| | `bot/services/spotify/matcher.py` | Candidate scoring (title/artist/duration) + per-source search via `YtDlpRunner`. |
| | `bot/services/podcast/meta.py` | Episode metadata via oEmbed (+ optional embed-page JSON), TTL-cached. |
| | `bot/services/podcast/itunes.py` | iTunes Search API lookup (storefront loop, cooldown, scoring). |
| | `bot/services/podcast/feed.py` | Defensive RSS/Atom enclosure parsing (`defusedxml`, caps). |
| | `bot/services/podcast/resolver.py` | Resolution order iTunes → RSS → labelled matcher fallback; TTL-cached. |
| | `bot/services/podcast/scoring.py` | Percentage-based episode scoring (title/show-weighted). |
| | `bot/services/podcast/download.py` | Hardened enclosure fetch (validated redirects, pinned DNS, size cap, no cookies). |
| Deploy | `deploy.sh` / `install.sh` | systemd install/update; interactive wizard. |
| | `deploy/download-links/` | nginx `secure_link` config, secret include, `mint_link.py`. |
| | `deploy/home-socks/` | Tailscale/`ssh -D` SOCKS egress unit. |
| | `deploy/telegram-bot-api/` | self-hosted Bot API compose for >50 MB uploads. |

---

## 3. Request lifecycle (a plain URL)

1. **Detect** — `links.on_text_message` runs on any non-command text; `utils.extract_urls`
   pulls out `http(s)` URLs. Inline mode and `?start=dl_…` deep links enter the same
   `handle_urls` path.
2. **Validate** — scheme check, `normalize_url` (canonical cache key), then
   `check_url_safety` (SSRF: reject private/reserved IPs) and `validate_redirect_chain`
   (skip for known extractor hosts). Rate limit checked (`ratelimiter.check`).
3. **Info** — `YtDlpRunner.extract_info` runs `yt-dlp -J --skip-download` with the same
   cookies/proxy/JS-runtime options as a download. On an extractor-looking error the
   updater runs once and retries (`links._extract`). A bot-wall maps to a dedicated hint.
4. **Card + picker** — a `PendingFlow` (URL, info, tmpdir, prefix `dl`/`pl`) is stored in
   an in-memory dict keyed by user id. `video_quality_rows` builds one button per
   distinct height (plus a "Best"), each labelled with a size estimate; options over the
   upload limit are flagged with a `· link` marker. Audio MP3/M4A, Thumbnail and Cancel
   buttons are always present.
5. **Pick → download** — `on_format_pick` → `_run_download`: free-disk guard, one active
   job per user, then `JobQueue.enter()` for a worker slot. `YtDlpRunner.download` runs
   yt-dlp into the job tmpdir; `[download] …%` lines drive the progress edit.
6. **Deliver** — `_deliver`: if the total size fits the upload limit (with a 2% margin)
   send one `send_video`/`send_audio`; else hand over a signed link and keep the picker
   alive so a smaller quality can still be uploaded. A failed upload that a link could
   fix also falls back to a link. The `file_id` is cached for instant repeats.
7. **Cleanup** — tmpdir removed when consumed; a startup sweep and hourly maintenance
   loop drop stale dirs, expired cache/deeplinks and expired links.

Multi-item posts: gallery sites (`instagram`, `facebook`, `twitter`/`x`, `tumblr`,
`reddit`, `pinterest`) with several entries become a media group; `/playlist` downloads
up to `PLAYLIST_MAX_ITEMS` entries.

---

## 4. Configuration reference

All values come from the environment or a `.env` file (`Settings`, `bot/config.py`).
Only `BOT_TOKEN` is required; everything else degrades gracefully. Full annotated list
lives in [`.env.example`](../.env.example); defaults:

| Variable | Default | Notes |
| --- | --- | --- |
| `BOT_TOKEN` | — | Required. |
| `ADMIN_IDS` | empty | Comma-separated numeric IDs; admins bypass all limits. |
| `MAX_UPLOAD_MB` | 50 / 2000 | Auto-2000 when `BOT_API_URL` set; clamped to 2000. Legacy `MAX_MB`. |
| `BOT_API_URL` | empty | Local Bot API base (no `/bot<token>` suffix). |
| `BOT_API_DATA_DIR` | `/var/lib/telegram-bot-api` | Free space shown in `/health`. |
| `TELEGRAM_API_ID`/`_HASH` | empty | Credentials for the local server. |
| `LOCAL_API`/`LOCAL_API_BASE` | false / `http://telegram-bot-api:8081` | Legacy switches. |
| `MAX_JOBS` | 2 | Queue concurrency. |
| `PER_USER_DAILY_LIMIT` | 30 | Successful downloads per UTC day. |
| `COOLDOWN_SECONDS` | 3 | Between requests. |
| `MAX_DURATION_MIN` | 60 | Reject longer media. |
| `YT_MAX_CONCURRENCY` | 2 | Concurrent yt-dlp processes. |
| `INFO_TIMEOUT` / `DOWNLOAD_TIMEOUT` | 30 / 600 | Seconds. |
| `UPLOAD_TIMEOUT_SECONDS` | 3600 | Large-file uploads. |
| `CACHE_TTL_DAYS` | 30 | `file_id` cache TTL. |
| `AUTO_UPDATE_YTDLP` / `UPDATE_CHECK_HOURS` | true / 24 | Self-update loop. |
| `COOKIES_FILE` | empty | Netscape cookies.txt. |
| `PROXY_URL` | empty | e.g. `socks5h://user:pass@host:1080`. |
| `PROXY_URL_INSTAGRAM`/`_TIKTOK`/`_X`/`_SOUNDCLOUD`/`_SPOTIFY` | empty | Per-platform overrides; fall back to `PROXY_URL`. |
| `YTDLP_EXTRA_ARGS` | empty | Extra CLI flags (shell-quoted). |
| `FORCE_IPV4` / `FORCE_IPV6` | false | IPv6 applied to YouTube URLs only. |
| `YTDLP_JS_RUNTIME` | auto | `auto` = Deno → Node ≥22; legacy `JS_RUNTIME`. |
| `EGRESS_TEST_URL` | a YouTube video | Probed by `/health`. |
| `DOWNLOAD_LINK_*` | see below | Over-limit delivery (section 7). |
| `MIN_FREE_DISK_GB` | 5 | Refuse a job below this free space. |
| `DB_PATH` | `data/savevideo.db` | SQLite file. |
| `TEMP_DIR` | `tmp` | Per-job scratch dirs. |
| `LOG_LEVEL` | `INFO`. |
| `DEFAULT_LANGUAGE` | `en`. |
| `BROADCAST_RATE` | 25 | Messages per 0.05 s sleep in `/broadcast`. |
| `PLAYLIST_MAX_ITEMS` | 10. |
| `MAX_MEDIA_GROUP` | 10. |
| `PROGRESS_EDIT_INTERVAL` | 3.0 | Progress edit throttle (s). |
| `UPLOAD_LARGE_RUNNING_JOBS` | 1. |
| `ENABLE_SPOTIFY` | false | Spotify metadata-match flow (section 13). |
| `SPOTIFY_MATCH_SOURCES` | `youtube,soundcloud` | Match sources in priority order. |
| `SPOTIFY_MIN_SCORE` | 0.65 | Minimum candidate score to offer. |
| `SPOTIFY_DURATION_TOLERANCE_S` | 4 | Allowed length difference when scoring. |
| `SPOTIFY_ENABLE_COLLECTIONS` | false | Expand album/playlist/show/artist. |
| `SPOTIFY_MAX_ITEMS` | 25 | Cap on items per collection link. |
| `SPOTIFY_CLIENT_ID`/`_SECRET` | empty | Optional Web API creds (collections need them). |
| `SPOTIFY_META_CONCURRENCY` | 4 | Parallel metadata fetches for a collection. |
| `ENABLE_PODCASTS` | false | Podcast-episode resolution (section 14). |
| `PODCAST_MATCH_SOURCES` | `youtube,soundcloud` | Last-resort match sources. |
| `PODCAST_MIN_SCORE` | 0.65 | Minimum score for a publisher match. |
| `PODCAST_EXCLUSIVE_MIN_SCORE` | 0.8 | Stricter bar for a last-resort match. |
| `PODCAST_DURATION_TOL_MIN_S`/`_PCT` | 30 / 0.03 | Percentage-based duration tolerance. |
| `PODCAST_MAX_DURATION_MIN` | 240 | Longest episode accepted. |
| `PODCAST_MAX_ENCLOSURE_MB` | 1024 | Hard cap on what the bot will fetch; over it, the publisher URL is shown (never fetched). |
| `PODCAST_REGIONS` | `us,de` | iTunes storefronts to search, in order. |
| `PODCAST_MAX_FEED_BYTES`/`_ITEMS` | 10485760 / 200 | Feed size / item caps. |
| `PODCAST_ITUNES_COOLDOWN_S` | 300 | Cooldown per storefront after a 403/429. |

Derived helpers on `Settings`: `max_duration_s`, `max_mb` (legacy alias),
`is_local_api`, `upload_limit_bytes`, `fits_upload_limit()`, `cache_ttl_s`,
`download_link_*`, `min_free_disk_bytes`, `data_dir`, `temp_dir_resolved`,
`is_admin()`, `spotify_sources()`, `proxy_for_spotify()`,
`podcast_regions_list()`, `proxy_for_podcast()`, `podcast_max_enclosure_bytes`.

---

## 5. Egress, proxy and JS runtime

**Proxy selection** (`downloader._platform_proxy`): per-URL host decides. Instagram,
TikTok and X have dedicated overrides; everything else uses `PROXY_URL`. When a proxy
is set it is passed as `--proxy`; the IPv4/IPv6 force flags are only applied when going
direct (they would fight the proxy's routing). `mask_proxy`/`format_proxy_summary`
render masked values for logs and `/health`; `mask_ip` hides the host bits of an exit IP.

**Cookies** (`_cookie_args`): the configured file may live read-only and be shared, so
it is copied into a private `.cookies/` inside the job tmpdir (mode 600) for each run.

**JS runtime** (`detect_js_runtime`): `auto` prefers Deno, then Node ≥ 22 (older Node is
skipped). Used via `--js-runtimes`. Purpose: solve YouTube's `n`/signature challenge and
mint the proof-of-origin (PO) token with yt-dlp's bundled **EJS** solver. EJS runs in the
yt-dlp process and therefore honours `--proxy`; no external POT server is required. This
replaced an earlier external `bgutil` provider, which ran on the bot's own IP and hung.

**Probes** for `/health`: `probe_egress` does a live metadata-only yt-dlp call through the
configured options; `probe_proxy_egress` fetches the public exit IP through the proxy
(via curl_cffi, else the `curl` binary). `classify_egress_error` reduces stderr to a short
class (`bot-wall`, `network`, `timeout`, `no-formats`, `error`).

**Anti-blocking playbook** (see README): datacenter IPs hit YouTube's bot-wall. The
supported combination is fresh cookies + a JS runtime (EJS). Alternatively route the bot
through a non-datacenter egress — a home machine over Tailscale with an `ssh -D` SOCKS
server (`deploy/home-socks`) or a paid sticky residential/mobile proxy. Per-platform
overrides let only the sites that need it use a metered egress.

---

## 6. Format / quality builder

`video_quality_rows(info, prefix, limit_bytes)` collects the distinct video heights from
the real yt-dlp formats, sorts descending, and emits one `FormatOption` row per height
keyed `"{prefix}:video_{h}"` plus a `"{prefix}:video_best"` row ("Best" is never capped).
Size estimates come from `estimate_format_size`/`best_estimate` (declared/approximate
filesize, else `tbr×duration`, else video+audio). Options over `limit_bytes` are flagged
`over_limit` (never hidden) so the picker can mark them for link delivery. Audio-only
sources yield no video rows, so the picker shows only the audio/thumbnail/cancel buttons.

`YtDlpRunner._format_args`: `video_best` → `bv*+ba/b`; `video_<h>` →
`bv*[height<=h]+ba/b[height<=h]`; `audio_mp3` → `ba/b` + mp3; `audio_m4a` →
`ba[ext=m4a]/ba/b` + m4a. Video downloads add `Merger:-movflags +faststart`.

---

## 7. Over-limit delivery: signed links + nginx `secure_link`

Files larger than the Bot API limit are never split or re-encoded. `DownloadLinkStore`
stores the finished file under `DOWNLOAD_LINK_DIR/<link_id>/<filename>` (an instant
`rename` when that dir and `TEMP_DIR` share a filesystem) and mints a signed URL.

Token formula (must match the nginx directive exactly — order: expires, uri, secret):

```
token = base64url( md5( f"{expires}{uri} {secret}" ) )   # '=' padding stripped
url   = f"{base}{uri}?md5={token}&expires={expires}"
```

nginx side:

```nginx
location /dl/ {
    secure_link $arg_md5,$arg_expires;
    secure_link_md5 "$secure_link_expires$uri $secret";
    if ($secure_link = "") { return 403; }
    if ($secure_link = "0") { return 410; }
    # serve $uri from DOWNLOAD_LINK_DIR
}
```

Config: `DOWNLOAD_LINK_BASE_URL` (public origin), `DOWNLOAD_LINK_SECRET` (matches the
nginx include), `DOWNLOAD_LINK_DIR`, `DOWNLOAD_LINK_TTL_HOURS` (6),
`DOWNLOAD_LINK_MAX_ACTIVE` (per user, 3), `DOWNLOAD_LINK_MAX_TOTAL_GB` (20). Quotas raise
`LinkError(reason)` → localised messages. `DownloadLinkStore.cleanup` reconciles files and
rows (expired, orphaned, missing). No `sec` token component is used, so there is no
nginx `secure_link` `$secure_link_secret`/etag dependency.

---

## 8. Bot API modes

- **Cloud** (default): 50 MB upload cap (`MAX_UPLOAD_MB` defaults to 50).
- **Local server**: set `BOT_API_URL` (empty path → `MAX_UPLOAD_MB` defaults to 2000,
  always clamped to the 2000 MB ceiling). The bot passes the absolute path of a finished
  file to the server, so `TEMP_DIR` and the server must share the same path (both compose
  files mount it). Switching requires a one-time `…/logOut` of the cloud API; cloud and
  local cannot run concurrently (`deploy/telegram-bot-api`, `docs/MIGRATION.md`).

---

## 9. Database

SQLite, WAL mode, foreign keys on. Migrations are applied in version order on startup
(`Database._migrate`); every statement is idempotent (`IF NOT EXISTS`), the applied
version is recorded in `schema_version`.

| Version | Contents |
| --- | --- |
| 1 | `users`, `downloads`, `cache`, `deeplinks`, `counters`, `schema_version` + indexes. |
| 2 | `download_links` (signed-link registry) + indexes. |
| 3 | `spotify_meta` (`spotify_id` PK, `type`, `json`, `fetched_at`) + `idx_spotify_meta_fetched`. |
| 4 | `podcast_meta`, `podcast_audio`, `podcast_itunes` (episode metadata / resolved audio caches + per-storefront cooldowns). |


- `cache` is keyed `(url_hash, format_key)` → `file_id`; `url_hash = sha256(normalized_url)`.
- `counters` holds `cache:hit` / `cache:miss` for the stats hit-rate.
- `downloads` records every attempt (status `ok`/`link`/`error`, error class, size,
  duration) and drives daily-quota counting and `/stats`.
- `spotify_meta` caches resolved Spotify metadata JSON with a `fetched_at` stamp; reads
  honour `META_TTL_S` (24 h) and the maintenance loop purges expired rows.
- `podcast_meta` / `podcast_audio` cache episode metadata and resolved audio (24 h);
  `podcast_itunes` stores a per-storefront cooldown after an iTunes 403/429. The
  maintenance loop purges the two caches.

---

## 10. Deploy & ops

- `deploy.sh` (idempotent, root): creates the `savevideo` user, syncs code via
  `git archive HEAD`, provisions the venv + requirements, fixes permissions, ensures
  `DOWNLOAD_LINK_DIR`, installs the systemd unit (substituting user/paths) and restarts.
  `.env` is gitignored and preserved. `SKIP_RESTART=1` updates without restarting.
- `install.sh` is the interactive wizard (Bot API config, packages, service).
- systemd hardening (`savevideo-bot.service`): `User=savevideo`, `ProtectSystem=full`,
  `PrivateTmp=true`, `RestrictAddressFamilies=AF_INET AF_INET6`,
  `ReadWritePaths=…/data …/tmp …/.venv /srv/savevideo-links`.
- Background tasks: hourly maintenance (purge deeplinks/cache/links + `sweep_stale_tmp`) and
  the yt-dlp auto-update loop (notifies admins on failure). Temp dirs are also swept once at
  startup. `sweep_temp_dir` (in `bot/services/downloader.py`) never removes dirs for live flows
  (`active_temp_dirs`) or anything under `DOWNLOAD_LINK_DIR`.
- Admin `/cleanup [max_age_s]` frees temp space on demand: builds the same protection set and
  reports entries removed, bytes freed, temp size and free disk before/after.
- Admin `/health` reports yt-dlp/ffmpeg versions, disk, queue, mode, upload limit, SQLite
  access, proxy summary (masked), cookies age, JS runtime, curl_cffi, IP family, Spotify
  status, extra args, a live egress probe and the masked proxy exit IP.

---

## 11. Known limits & caveats

- **Region-locked content** fails on any IP (direct or via a home/mobile egress) when the
  platform itself geo-restricts the item.
- **YouTube bot-wall** persists without cookies + a JS runtime or a non-datacenter egress;
  `FORCE_IPV6` is not a fix.
- **DRM platforms (e.g. Spotify)** cannot be downloaded: there is no yt-dlp extractor and
  the streams are protected. See the Spotify section.
- The `yt_max_concurrency` semaphore and the per-user one-job rule are in-process; running
  multiple bot instances against one DB would not share them.
- Signed links are bearer URLs: anyone with the URL can download until it expires.

---

## 12. SoundCloud

SoundCloud is handled by yt-dlp's built-in `soundcloud` extractor, so no special
code path is needed. `soundcloud.com` and subdomains are listed in
`utils._KNOWN_EXTRACTOR_HOSTS` (the SSRF redirect probe is skipped) and in
`utils.site_from_url` (stats label `soundcloud`). `downloader._SOUNDCLOUD_HOSTS`
covers `soundcloud.com` and `api.soundcloud.com` for proxy selection:
`PROXY_URL_SOUNDCLOUD` → `PROXY_URL` → direct.

A track has no video streams, so `video_quality_rows` returns `[]` and the picker
shows only Audio (MP3/M4A), Thumbnail and Cancel — the intended audio-only UX.
Sets/playlists are downloaded through `/playlist` (capped at `PLAYLIST_MAX_ITEMS`)
and sent sequentially as audio.

## 13. Spotify

The bot never downloads from Spotify: its streams are DRM-protected and there
is no yt-dlp extractor. With `ENABLE_SPOTIFY=true`, a Spotify link is instead
*metadata-matched* to a downloadable equivalent from another source, and the
normal audio pipeline (picker → download → tag → upload/cache/link) is reused.

**Routing.** `spotify.router` is registered **before `links.router`** and
**regardless of the flag**, so a Spotify URL is always intercepted and never
falls through to the generic link flow. With the feature off the handler replies
with `spotify_disabled`. The filter (`SpotifyLinkFilter`) only claims URLs whose
host is a Spotify host, so every other link is untouched.

**Pipeline** (`on_spotify_message`): resolve the URL
(`urls.parse_spotify_url`; short `spotify.link`/`spoti.fi` links are followed
once) → **metadata** (`metadata.fetch_metadata`) → **match**
(`matcher.find_match`) → **present** the audio picker.

- **Metadata** (`spotify/metadata.py`): tries oEmbed first, then page
  `<meta>`/JSON-LD tags, and (when credentials are set) the Web API, merging all
  three into a `TrackMeta`. Results are cached in `spotify_meta` for
  `META_TTL_S` (24 h). Only the public page is read; no credentials are required
  for single tracks.
- **Matching** (`spotify/matcher.py`): builds a query from title + primary
  artist, searches each configured source in priority order through
  `YtDlpRunner.extract_info` (explicit per-source `proxy` so a search URL with
  no host is still routed correctly), then scores each candidate: title `0.60`,
  artist `0.25`, duration `0.15` (duration scored within
  `SPOTIFY_DURATION_TOLERANCE_S`). The best candidate at or above
  `SPOTIFY_MIN_SCORE` wins. A per-source `breaker.Breaker` (3 failures / 300 s
  cooldown) skips a source that is failing, and YouTube is tried after
  SoundCloud so the bot still works while YouTube is bot-walled.
- **Present** (`_present`): stores a `PendingFlow` with the matched URL, a
  `cache_key` of `spotify:<id>` (so the same track reuses the cached upload even
  across different source URLs), an `AudioMeta` (title/artist/cover) and a
  `postprocess` hook, then shows the reusable audio picker (`links.picker_keyboard`).

**Tagging** (`spotify/tagger.py`): on download the `postprocess` hook fetches the
cover and calls `tag_audio`, which runs ffmpeg with an **argument list (never a
shell)** to write ID3 tags + attached picture (MP3) or an `attached_pic` stream
(M4A). Tag values from untrusted pages are sanitised (`sanitize_tag`). Tagging is
best-effort: if the cover embed fails the bot still sends the file with text
tags.

**Collections** (album/playlist/show/artist): only when
`SPOTIFY_ENABLE_COLLECTIONS=true` **and** Web API credentials are configured
(the API is needed to enumerate tracks). Up to `SPOTIFY_MAX_ITEMS` items are
resolved, matched and downloaded sequentially through `links.download_flow`;
per-item failures are counted and reported.

**Health**: `/health` shows `Spotify: off` or `on [sources] api|no-api`, and the
masked `PROXY_URL_SPOTIFY` override appears in the proxy summary.

---

## 14. Podcast episodes

Spotify never serves the audio here either. With `ENABLE_PODCASTS=true` (default
off), a Spotify **episode** link is resolved to the publisher's own audio and
sent as-is — tagged, never transcoded. The router is shared with section 13
(`spotify.router`, always registered, before `links.router`), so episode links
are intercepted whether or not `ENABLE_SPOTIFY` is set.

**Gating.** The podcast and Spotify lanes are independent:

- **episode + `ENABLE_PODCASTS=false`** → `podcast_disabled` reply; the link
  never reaches `links.router`/yt-dlp.
- **show + `ENABLE_PODCASTS=true`** → `podcast_show_link` ("send an episode
  link"); single episodes only in v1. With the flag off a show link falls through
  to the Spotify collection path as before (so section 13 behaviour is unchanged
  when the podcast feature is off).
- music/collection kinds are untouched by this section.

**Resolution order** (`services/podcast/resolver.py`, cached 24 h in
`podcast_audio`):

1. **iTunes Search** (`podcast/itunes.py`): `media=podcast&entity=podcastEpisode`
   returns the publisher's own `episodeUrl` (the RSS enclosure). Storefronts from
   `PODCAST_REGIONS` are searched in order; an episode's title and show name are
   sent to Apple. A 403/429 stores a per-region cooldown in `podcast_itunes`,
   honoured by later searches. Candidates are scored; the best at or above
   `PODCAST_MIN_SCORE` wins.
2. **RSS/Atom enclosures** (`podcast/feed.py`): the show feed (from iTunes's
   `feedUrl`, else discovered) is parsed with **`defusedxml`** — never the stdlib
   parser — bounded by `PODCAST_MAX_FEED_BYTES` and `PODCAST_MAX_FEED_ITEMS`.
3. **Matched copy** (`find_fallback_match`): only when no publisher copy exists
   (typically a Spotify-exclusive). Searches `PODCAST_MATCH_SOURCES` through the
   existing matcher and requires the stricter `PODCAST_EXCLUSIVE_MIN_SCORE`. The
   result is `is_fallback=True` and the picker is **labelled** ("matched copy
   from …, may differ"). If nothing clears the bar the user gets a clear
   `podcast_no_match` ("not available outside Spotify") message.

Episode **metadata** (`podcast/meta.py`) is credential-free and best-effort:
oEmbed (title + cover) is required; the embed page's `__NEXT_DATA__` JSON (show
name + exact duration) is **optional** and only merged when present. Results are
cached in `podcast_meta` for 24 h.

**Scoring** (`podcast/scoring.py`) differs from the music matcher because
dynamic ad insertion changes lengths: duration uses a **percentage** tolerance
`max(PODCAST_DURATION_TOL_MIN_S, PODCAST_DURATION_TOL_PCT * duration)` and title
`0.45` / show `0.35` outweigh duration `0.20`; components with missing data are
renormalised out.

**Enclosure download** (`podcast/download.py`) is deliberately **not** yt-dlp:
the generic extractor would follow redirects itself, bypassing SSRF checks. The
fetcher instead:

- follows redirects manually (max 5) and revalidates **every hop**;
- resolves each host and blocks private/loopback/link-local/metadata addresses
  (`utils.ip_is_public`), then pins the connection to the **validated** address
  via a custom resolver (closes the DNS-rebinding window);
- requires an `audio/*` or `application/octet-stream` body (a present but
  non-audio content type is rejected, so error pages are not saved);
- streams to disk under a hard byte cap (`PODCAST_MAX_ENCLOSURE_MB`) and a total
  time cap, deleting partial files on any failure;
- sends **no cookies** (`DummyCookieJar`) and ignores ambient proxy env.

The two size limits are deliberately separate:

- **Over `MAX_UPLOAD_MB` (Telegram), after a successful download.** The file is
  on disk, so `links.deliver_local_file` hands it to the signed hosted-link flow
  (section 7) instead of uploading.
- **Over `PODCAST_MAX_ENCLOSURE_MB`, during the download.** Nothing is on disk,
  so `EnclosureTooLarge` is answered with the publisher's own URL as plain text
  (`podcast_too_large_link`, link previews disabled) rather than a dead-end
  error. The URL comes from untrusted feed data, so it is sanitised first
  (`utils.safe_display_url`): http/https only, credentials and control/whitespace
  characters stripped, length capped — and it is **never fetched**. The matcher
  fallback is not used here (a different copy would be just as large). The disk
  free-space check runs **before** the download starts, so a large enclosure is
  refused up front when the disk is low.

A downloaded enclosure is delivered by `links.deliver_local_file`, which reuses
the shared upload path: over the Telegram limit (or on an upload that a link
would fix) it is handed over as a signed download link (section 7); otherwise it
is uploaded. Tagging writes title, show (artist **and** album) and the release
date, with cover art, via ffmpeg argument lists.

**Privacy / data sharing.** An episode's title and show name are sent to Apple's
iTunes Search API to locate the publisher copy. This is stated in the README and
`.env.example`.
