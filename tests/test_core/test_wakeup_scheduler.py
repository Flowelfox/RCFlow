"""Unit tests for :mod:`src.core.wakeup_scheduler`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.core.session import ScheduledWake
from src.core.wakeup_scheduler import WakeupScheduler


def _wake(delay_seconds: float) -> ScheduledWake:
    now = datetime.now(UTC)
    return ScheduledWake(
        wake_id=f"w-{delay_seconds}",
        prompt="hi",
        reason="test",
        fire_at=now + timedelta(seconds=delay_seconds),
        created_at=now,
    )


@pytest.mark.asyncio
async def test_arm_fires_callback():
    fired: list[tuple[str, ScheduledWake]] = []

    async def on_fire(sid: str, w: ScheduledWake) -> None:
        fired.append((sid, w))

    scheduler = WakeupScheduler(on_fire)
    scheduler.arm("sid-1", _wake(0.05))
    await asyncio.sleep(0.2)
    assert len(fired) == 1
    assert fired[0][0] == "sid-1"


@pytest.mark.asyncio
async def test_cancel_drops_timer():
    fired: list[ScheduledWake] = []

    async def on_fire(_sid: str, w: ScheduledWake) -> None:
        fired.append(w)

    scheduler = WakeupScheduler(on_fire)
    w = _wake(0.2)
    scheduler.arm("sid-1", w)
    scheduler.cancel(w.wake_id)
    await asyncio.sleep(0.3)
    assert fired == []


@pytest.mark.asyncio
async def test_arm_replaces_existing_timer():
    fired: list[str] = []

    async def on_fire(_sid: str, w: ScheduledWake) -> None:
        fired.append(w.prompt)

    scheduler = WakeupScheduler(on_fire)
    first = ScheduledWake(
        wake_id="w-1",
        prompt="first",
        reason="",
        fire_at=datetime.now(UTC) + timedelta(seconds=1.0),
        created_at=datetime.now(UTC),
    )
    second = ScheduledWake(
        wake_id="w-1",  # same id — re-arm
        prompt="second",
        reason="",
        fire_at=datetime.now(UTC) + timedelta(seconds=0.05),
        created_at=datetime.now(UTC),
    )
    scheduler.arm("sid-1", first)
    scheduler.arm("sid-1", second)
    await asyncio.sleep(0.2)
    # Only the second timer should have fired.
    assert fired == ["second"]


@pytest.mark.asyncio
async def test_past_due_fires_immediately():
    fired: list[ScheduledWake] = []

    async def on_fire(_sid: str, w: ScheduledWake) -> None:
        fired.append(w)

    scheduler = WakeupScheduler(on_fire)
    past = ScheduledWake(
        wake_id="w-past",
        prompt="hi",
        reason="",
        fire_at=datetime.now(UTC) - timedelta(seconds=10),
        created_at=datetime.now(UTC),
    )
    scheduler.arm("sid-1", past)
    await asyncio.sleep(0.05)
    assert len(fired) == 1
