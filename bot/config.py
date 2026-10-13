"""Bot configuration loaded from environment variables (and an optional `.env` file).

Only ``BOT_TOKEN`` is strictly required. Every other setting has a sensible
default so the bot starts and degrades gracefully on a bare VPS.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    admin_ids: list[int] = []

    # --- Upload limits ---
    max_mb: int = 50

    # --- Local Bot API ---
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

    # --- Cache ---
    cache_ttl_days: int = 30

    # --- Maintenance ---
    auto_update_ytdlp: bool = True
    update_check_hours: int = 24

    # --- Anti-blocking ---
    cookies_file: str = ""
    proxy_url: str = ""

    # --- Storage ---
    db_path: str = "data/savevideo.db"
    temp_dir: str = "tmp"
    log_level: str = "INFO"

    # --- Localisation ---
    default_language: str = "en"

    # --- Behaviour constants ---
    broadcast_rate: int = 25
    playlist_max_items: int = 10
    max_media_group: int = 10
    progress_edit_interval: float = 3.0
    upload_large_running_jobs: int = 1

    @field_validator("admin_ids", mode="before")
    @classmethod
    def split_admin_ids(cls, raw: object) -> list[int]:
        return _field_validator_helper(raw)

    # ---- Derived helpers ---------------------------------------------------

    @property
    def max_duration_s(self) -> int:
        return self.max_duration_min * 60

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_mb * 1024 * 1024

    @property
    def cache_ttl_s(self) -> int:
        return self.cache_ttl_days * 24 * 3600

    @property
    def installs_local_api(self) -> bool:
        return self.local_api or bool(self.telegram_api_id)

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