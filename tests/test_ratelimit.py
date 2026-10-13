from __future__ import annotations

from bot.services.ratelimit import LimitReason, RateLimiter


async def _record_ok(db, user_id: int, n: int) -> None:
    for i in range(n):
        await db.record_download(user_id, f"h{i}", "youtube", "video_720", "ok", None, 1, 1)


async def test_admin_is_exempt(settings, db):
    rl = RateLimiter(db, settings)
    status = await rl.check(999)  # ADMIN_ID
    assert status.allowed
    assert await rl.start_job(999) is True


async def test_cooldown_blocks_second_request(db, settings):
    rl = RateLimiter(db, settings)
    status = await rl.check(1)
    assert status.allowed
    await rl.begin_request(1)
    status = await rl.check(1)
    assert not status.allowed
    assert status.reason is LimitReason.COOLDOWN
    assert status.seconds >= 1


async def test_daily_limit(db, settings):
    for _ in range(settings.per_user_daily_limit):
        await db.record_download(1, "h", "youtube", "video_best", "ok", None, 1, 1)
    rl = RateLimiter(db, settings)
    status = await rl.check(1)
    assert not status.allowed
    assert status.reason is LimitReason.DAILY


async def test_active_job_lifecycle(settings, db):
    rl = RateLimiter(db, settings)
    assert await rl.has_active_job(1) is False
    assert await rl.start_job(1) is True
    assert await rl.start_job(1) is False  # already active
    assert await rl.has_active_job(1) is True
    await rl.finish_job(1)
    assert await rl.has_active_job(1) is False


async def test_admin_bypasses_active_job(settings, db):
    rl = RateLimiter(db, settings)
    assert await rl.start_job(999) is True
    assert await rl.has_active_job(999) is False
