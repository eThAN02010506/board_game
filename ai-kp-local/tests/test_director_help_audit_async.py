from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ai_kp.api.director_help_audits import run_director_help_audit_io


def test_audit_io_runs_without_blocking_the_event_loop() -> None:
    async def exercise() -> None:
        audit_task = asyncio.create_task(run_director_help_audit_io(time.sleep, 0.2))
        loop = asyncio.get_running_loop()
        heartbeat_started = loop.time()
        await asyncio.sleep(0.03)
        assert loop.time() - heartbeat_started < 0.1
        assert not audit_task.done()
        await audit_task

    asyncio.run(exercise())


def test_audit_io_finishes_started_work_before_propagating_cancellation() -> None:
    started = threading.Event()
    finished = threading.Event()

    def blocking_write() -> None:
        started.set()
        time.sleep(0.08)
        finished.set()

    async def exercise() -> None:
        audit_task = asyncio.create_task(run_director_help_audit_io(blocking_write))
        await asyncio.to_thread(started.wait, 1)
        audit_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await audit_task

    asyncio.run(exercise())
    assert finished.is_set()


def test_audit_io_survives_repeated_cancellation_while_draining() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_write() -> None:
        started.set()
        release.wait(timeout=1)
        finished.set()

    async def exercise() -> None:
        audit_task = asyncio.create_task(run_director_help_audit_io(blocking_write))
        await asyncio.to_thread(started.wait, 1)
        audit_task.cancel()
        await asyncio.sleep(0)
        audit_task.cancel()
        await asyncio.sleep(0)
        assert not audit_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await audit_task

    asyncio.run(exercise())
    assert finished.is_set()
