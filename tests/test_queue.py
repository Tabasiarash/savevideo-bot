from __future__ import annotations

import asyncio

from bot.services.queue import JobQueue


async def test_enter_immediately_when_free():
    q = JobQueue(max_jobs=2)
    a1 = await q.enter()
    a2 = await q.enter()
    assert a1.waited is False and a1.position == 1
    assert a2.waited is False
    assert q.active == 2
    await q.leave()
    await q.leave()
    assert q.active == 0


async def test_enter_waits_when_full_then_proceeds():
    q = JobQueue(max_jobs=1)
    await q.enter()
    order: list[str] = []

    async def waiter():
        adm = await q.enter()
        order.append(f"run{adm.position} waited={adm.waited}")
        await q.leave()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    assert q.waiting == 1
    assert order == []  # still blocked
    await q.leave()  # free the slot
    await asyncio.wait_for(task, timeout=1)
    assert order == ["run1 waited=True"]


async def test_max_jobs_is_at_least_one():
    q = JobQueue(max_jobs=0)
    adm = await q.enter()
    assert adm.position == 1
    await q.leave()


async def test_cancelled_waiter_decrements_waiting():
    q = JobQueue(max_jobs=1)
    await q.enter()

    async def waiter():
        await q.enter()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    assert q.waiting == 1
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task
    assert q.waiting == 0
    await q.leave()
