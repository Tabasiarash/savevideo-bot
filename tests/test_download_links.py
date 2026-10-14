from __future__ import annotations

import time

import pytest
from bot.services.download_links import (
    LinkError,
    safe_filename,
    secure_link_token,
)


def source_file(tmp_path, name: str, size: int):
    path = tmp_path / name
    path.write_bytes(b"\x00" * size)
    return path


def test_secure_link_token_matches_nginx_formula():
    # Equivalent nginx/openssl computation:
    #   echo -n '1700000000/dl/test/hello.txt secret' \
    #     | openssl md5 -binary | openssl base64 | tr +/ -_ | tr -d =
    assert secure_link_token("/dl/test/hello.txt", 1700000000, "secret") == "T7x3rByjPofOHsks2TnaGg"


def test_safe_filename():
    assert safe_filename("Ünïcode name!.mp4") == "n_code_name_.mp4"
    assert safe_filename("....") == "download"
    assert safe_filename("") == "download"
    assert len(safe_filename("x" * 500)) == 120


async def test_create_moves_file_and_mints_a_url(services, tmp_path):
    source = source_file(tmp_path, "media.mp4", 1024)

    result = await services.download_links.create(
        services.db, source=source, user_id=42, title="T"
    )

    assert result.size_bytes == 1024
    assert result.url.startswith("http://links.test/dl/")
    assert "md5=" in result.url and "expires=" in result.url
    assert not source.exists()  # moved into place, not copied
    stored = list(services.settings.download_link_dir_resolved.rglob("media.mp4"))
    assert len(stored) == 1
    rows = await services.db.download_link_all()
    assert len(rows) == 1
    assert rows[0]["rel_path"] == f"{stored[0].parent.name}/media.mp4"


async def test_create_disabled_without_base_url(services, monkeypatch, tmp_path):
    monkeypatch.setattr(services.settings, "download_link_base_url", "", raising=False)
    source = source_file(tmp_path, "m.mp4", 10)
    with pytest.raises(LinkError) as exc:
        await services.download_links.create(services.db, source=source, user_id=1, title="T")
    assert exc.value.reason == "disabled"


async def test_create_user_quota(services, tmp_path):
    services.settings.download_link_max_active = 1
    now = int(time.time())
    await services.db.download_link_create(
        token="t",
        user_id=42,
        filename="a",
        rel_path="a/a",
        size_bytes=1,
        created_at=now,
        expires_at=now + 3600,
    )
    source = source_file(tmp_path, "m.mp4", 10)
    with pytest.raises(LinkError) as exc:
        await services.download_links.create(services.db, source=source, user_id=42, title="T")
    assert exc.value.reason == "quota_user"


async def test_create_total_quota(services, monkeypatch, tmp_path):
    monkeypatch.setattr(services.settings, "download_link_max_total_gb", 1e-9, raising=False)
    source = source_file(tmp_path, "m.mp4", 10)
    with pytest.raises(LinkError) as exc:
        await services.download_links.create(services.db, source=source, user_id=42, title="T")
    assert exc.value.reason == "quota_total"


async def test_cleanup_removes_expired_and_orphans(services, tmp_path):
    root = services.settings.download_link_dir_resolved
    now = int(time.time())

    expired = root / "expired" / "old.mp4"
    expired.parent.mkdir(parents=True, exist_ok=True)
    expired.write_bytes(b"x")
    await services.db.download_link_create(
        token="exp",
        user_id=1,
        filename="old.mp4",
        rel_path="expired/old.mp4",
        size_bytes=1,
        created_at=now - 10,
        expires_at=now - 1,
    )

    orphan = root / "orphan" / "keep.mp4"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"y")

    live = source_file(tmp_path, "live.mp4", 5)
    await services.download_links.create(services.db, source=live, user_id=1, title="T")

    await services.download_links.cleanup(services.db)

    assert not expired.exists()
    assert not orphan.exists()
    rows = await services.db.download_link_all()
    assert len(rows) == 1
    assert (root / rows[0]["rel_path"]).exists()
