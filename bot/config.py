"""Bot configuration loaded from environment variables (and an optional `.env` file).

Only ``BOT_TOKEN`` is strictly required. Every other setting has a sensible
default so the bot starts and degrades gracefully on a bare VPS.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from bot.utils import fits_upload_limit as _fits_upload_limit

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Telegram's local Bot API server refuses uploads larger than 2000 MB.
UPLOAD_CEILING_MB = 2000


def _field_validator_helper(raw: object) -> list[int]:
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw if str(x).strip()]
    if raw is None:
        return []
    parts = str(raw).split(",")
    return [int(p.strip()) for p in parts if p.strip()]


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment / ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Required ---
    bot_token: str = ""

    # --- Admins ---
    admin_ids: Annotated[list[int], NoDecode] = []

    # --- Upload limits ---
    # Preferred name is MAX_UPLOAD_MB; MAX_MB is still accepted for backward
    # compatibility. When a local Bot API server is configured and no explicit
    # value is given it defaults to 2000 MB (never more).
    max_upload_mb: int = Field(
        default=50,
        validation_alias=AliasChoices("MAX_UPLOAD_MB", "MAX_MB", "max_upload_mb", "max_mb"),
    )

    # --- Local Bot API ---
    # Point this at a self-hosted telegram-bot-api server, e.g.
    # http://127.0.0.1:8081. Empty means the public cloud API (50 MB cap).
    bot_api_url: str = ""
    # Directory the local Bot API server keeps its files in (/health reports its
    # free space).
    bot_api_data_dir: str = "/var/lib/telegram-bot-api"
    # Legacy switches, still honoured: LOCAL_API=true makes the bot use
    # LOCAL_API_BASE as BOT_API_URL.
    local_api: bool = False
    local_api_base: str = "http://telegram-bot-api:8081"
    telegram_api_id: int = 0
    telegram_api_hash: str = ""

    # --- Concurrency and per-user limits ---
    max_jobs: int = 2
    per_user_daily_limit: int = 30
    cooldown_seconds: float = 3.0
    max_duration_min: int = 60

    # --- Timeouts (seconds) ---
    info_timeout: int = 30
    download_timeout: int = 600
    # Upload timeout. Large files through a local Bot API server can take a
    # long while, so this is much higher than the aiohttp default of 60s.
    upload_timeout_seconds: int = 3600

    # --- Cache ---
    cache_ttl_days: int = 30

    # --- Maintenance ---
    auto_update_ytdlp: bool = True
    update_check_hours: int = 24

    # --- Anti-blocking ---
    cookies_file: str = ""
    proxy_url: str = ""
    # Optional per-platform proxy overrides. Each falls back to PROXY_URL when
    # unset. Useful for sending only the sites that need it (e.g. YouTube,
    # TikTok) through a metered home/mobile egress while the rest goes direct.
    proxy_url_instagram: str = ""
    proxy_url_tiktok: str = ""
    proxy_url_x: str = ""
    proxy_url_soundcloud: str = ""
    # Extra yt-dlp flags, shell-quoted, appended last (may override defaults).
    ytdlp_extra_args: str = ""
    # Force an IP family. IPv6 is only ever applied to YouTube URLs because a
    # global `--force-ipv6` breaks IPv4-only sites (e.g. Vimeo).
    force_ipv4: bool = False
    force_ipv6: bool = False
    # JavaScript runtime for yt-dlp's nsig/PO-token solving: "auto" (prefer deno
    # then node >= 22), or an explicit value such as "deno", "node",
    # "node:/usr/bin/node". YTDLP_JS_RUNTIME is the preferred env name.
    js_runtime: str = Field(
        default="auto",
        validation_alias=AliasChoices("YTDLP_JS_RUNTIME", "JS_RUNTIME", "js_runtime"),
    )
    # URL used by the admin /health command to probe the real download egress.
    egress_test_url: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

    # --- Spotify (metadata match; feature is OFF by default) -----------------
    # When enabled, a Spotify link is turned into a metadata match: we read the
    # public title/artist/cover, find the same recording on an allowed source
    # (YouTube/SoundCloud) and download that copy. Spotify audio is never fetched.
    enable_spotify: bool = False
    # Comma-separated, priority-ordered search sources.
    spotify_match_sources: str = "youtube,soundcloud"
    # Minimum matcher score (0..1) for a candidate to be accepted.
    spotify_min_score: float = 0.65
    # Allowed duration drift between Spotify and a candidate, in seconds.
    spotify_duration_tolerance_s: int = 4
    # Albums/playlists/shows/artists are only expanded when this is true.
    spotify_enable_collections: bool = False
    # Hard cap on the number of items taken from a collection.
    spotify_max_items: int = 25
    # Optional Spotify Web API credentials (fill gaps such as ISRC).
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # Optional proxy just for Spotify metadata requests (falls back to PROXY_URL).
    proxy_url_spotify: str = ""
    # Parallel metadata fetches when expanding a collection.
    spotify_meta_concurrency: int = 4

    # --- Podcasts (episode audio; independent feature, OFF by default) -------
    # Episodes are resolved from the publisher's public RSS/audio found via the
    # iTunes Search API. Spotify audio is never fetched; the enclosure is the
    # original publisher file, sent as-is (tagged, not transcoded).
    #
    # NOTE: an episode's title and show name are sent to Apple's iTunes Search
    # API when this feature is used.
    enable_podcasts: bool = False
    # Fallback search sources when no publisher enclosure is found.
    podcast_match_sources: str = "youtube,soundcloud"
    # Minimum matcher score (0..1) for a fallback (non-publisher) candidate.
    podcast_min_score: float = 0.65
    # Stricter bar for a Spotify-exclusive episode, where a YouTube copy is the
    # only option and a mismatch is more likely.
    podcast_exclusive_min_score: float = 0.8
    # Duration tolerance is percentage-based (dynamic ads change lengths):
    # a candidate within max(PODCAST_DURATION_TOL_MIN_S, PCT * duration) matches.
    podcast_duration_tol_min_s: int = 30
    podcast_duration_tol_pct: float = 0.03
    # Longest episode accepted (minutes). Telegram's upload cap still applies.
    podcast_max_duration_min: int = 240
    # Hard cap on a single enclosure download (bytes). This only stops abuse:
    # a 240-minute episode at 128 kbps is roughly 230 MB. Independent of the
    # Telegram upload limit — a file that downloads but exceeds the upload limit
    # is still delivered as a hosted link, while one over *this* cap is never
    # fetched and the publisher's own URL is shown instead.
    podcast_max_enclosure_mb: int = 1024
    # iTunes storefronts to search, in priority order (shows are regional).
    podcast_regions: str = "us,de"
    # RSS feed size / item caps (feeds are untrusted input).
    podcast_max_feed_bytes: int = 10 * 1024 * 1024
    podcast_max_feed_items: int = 200
    # Cooldown (seconds) after an iTunes rate-limit / error, per region.
    podcast_itunes_cooldown_s: int = 300
    # Optional proxy for podcast HTTP (metadata + enclosure), no cookies.
    proxy_url_podcast: str = ""

    # --- Storage ---
    db_path: str = "data/savevideo.db"
    temp_dir: str = "tmp"
    log_level: str = "INFO"
    # Refuse to start a download when the filesystem holding TEMP_DIR has less
    # than this much free space (4K files are large).
    min_free_disk_gb: float = 5.0

    # --- Over-limit delivery via signed download links ------------------------
    # Public origin that serves DOWNLOAD_LINK_DIR, e.g. https://dl.example.com.
    # Empty disables link delivery (oversized files are then rejected).
    download_link_base_url: str = ""
    # Shared secret for nginx `secure_link` (MD5). Must match the value in the
    # nginx secret include file. NEVER commit the real value.
    download_link_secret: str = ""
    # Directory served by nginx. Keep it on the SAME filesystem as TEMP_DIR so
    # moving a finished file is an instant rename, not a multi-GB copy.
    download_link_dir: str = "/srv/savevideo-links"
    # How long a link stays valid.
    download_link_ttl_hours: int = 6
    # Per-user cap on concurrently active links.
    download_link_max_active: int = 3
    # Global cap on the total size of stored link files.
    download_link_max_total_gb: float = 20.0

    # --- Localisation ---
    default_language: str = "en"

    # --- Behaviour constants ---
    broadcast_rate: int = 25
    playlist_max_items: int = 10
    max_media_group: int = 10
    progress_edit_interval: float = 3.0
    upload_large_running_jobs: int = 1
    # Maximum number of yt-dlp downloads running at once (YouTube throttles
    # clients that request too much too quickly).
    yt_max_concurrency: int = 2

    @field_validator("admin_ids", mode="before")
    @classmethod
    def split_admin_ids(cls, raw: object) -> list[int]:
        return _field_validator_helper(raw)

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def blank_int_to_zero(cls, raw: object) -> object:
        return 0 if raw in ("", None) else raw

    @model_validator(mode="after")
    def resolve_derived(self) -> Settings:
        # Legacy LOCAL_API/LOCAL_API_BASE still select the local server.
        if not self.bot_api_url and self.local_api:
            self.bot_api_url = self.local_api_base
        # Auto-raise the upload cap for a local server unless explicitly set,
        # then clamp to Telegram's hard ceiling.
        if "max_upload_mb" not in self.model_fields_set:
            self.max_upload_mb = UPLOAD_CEILING_MB if self.bot_api_url else 50
        self.max_upload_mb = max(1, min(self.max_upload_mb, UPLOAD_CEILING_MB))
        return self

    # ---- Derived helpers ---------------------------------------------------

    @property
    def max_duration_s(self) -> int:
        return self.max_duration_min * 60

    @property
    def max_mb(self) -> int:
        """Backward-compatible alias for :attr:`max_upload_mb`."""
        return self.max_upload_mb

    @property
    def is_local_api(self) -> bool:
        return bool(self.bot_api_url)

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def fits_upload_limit(self, size_bytes: int) -> bool:
        """True when ``size_bytes`` fits under the configured limit (2% margin)."""
        return _fits_upload_limit(size_bytes, self.upload_limit_bytes)

    @property
    def cache_ttl_s(self) -> int:
        return self.cache_ttl_days * 24 * 3600

    @property
    def installs_local_api(self) -> bool:
        return self.local_api or bool(self.telegram_api_id)

    @property
    def download_link_enabled(self) -> bool:
        """Link delivery needs a public base URL, a secret and a target dir."""
        return bool(
            self.download_link_base_url.strip() and self.download_link_secret and self.download_link_dir
        )

    @property
    def download_link_ttl_s(self) -> int:
        return max(1, int(self.download_link_ttl_hours)) * 3600

    @property
    def download_link_dir_resolved(self) -> Path:
        p = Path(self.download_link_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def min_free_disk_bytes(self) -> int:
        return int(self.min_free_disk_gb * 1024**3)

    def spotify_sources(self) -> list[str]:
        """Normalised, de-duplicated Spotify match sources (lower-case)."""
        seen: list[str] = []
        for part in self.spotify_match_sources.split(","):
            name = part.strip().lower()
            if name and name not in seen:
                seen.append(name)
        return seen

    def proxy_for_spotify(self) -> str:
        return self.proxy_url_spotify.strip() or self.proxy_url.strip()

    def podcast_regions_list(self) -> list[str]:
        """Normalised iTunes storefronts to search, in priority order."""
        seen: list[str] = []
        for part in self.podcast_regions.split(","):
            code = part.strip().lower()
            if len(code) == 2 and code.isalpha() and code not in seen:
                seen.append(code)
        return seen or ["us"]

    def proxy_for_podcast(self) -> str:
        return self.proxy_url_podcast.strip() or self.proxy_url.strip()

    @property
    def podcast_max_enclosure_bytes(self) -> int:
        return max(1, self.podcast_max_enclosure_mb) * 1024 * 1024

    @property
    def data_dir(self) -> Path:
        p = Path(self.db_path).resolve()
        return p.parent

    @property
    def temp_dir_resolved(self) -> Path:
        p = Path(self.temp_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
