from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_kp.bootstrap.composition import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.llm.director_help_call_gate import (
    DirectorHelpCallGate,
    DirectorHelpCapacityExceededError,
    DirectorHelpRunInProgressError,
)


def test_director_help_concurrency_defaults_to_one_and_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_KP_DIRECTOR_HELP_MAX_CONCURRENCY", raising=False)

    assert Settings(_env_file=None).director_help_max_concurrency == 1
    assert (
        Settings(
            _env_file=None,
            director_help_max_concurrency=32,
        ).director_help_max_concurrency
        == 32
    )
    for invalid in (0, 33):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, director_help_max_concurrency=invalid)
        with pytest.raises(ValueError, match="integer from 1 to 32"):
            DirectorHelpCallGate(invalid)


def test_gate_rejects_same_run_and_full_capacity_without_queueing() -> None:
    async def exercise() -> None:
        gate = DirectorHelpCallGate(2)

        async with gate.reserve("run-1"):
            async with gate.reserve("run-2"):
                assert gate.active_count() == 2
                with pytest.raises(DirectorHelpRunInProgressError) as duplicate:
                    async with gate.reserve("run-1"):
                        raise AssertionError("duplicate reservation was admitted")
                assert duplicate.value.code == "director_help_in_progress"

                with pytest.raises(DirectorHelpCapacityExceededError) as capacity:
                    async with gate.reserve("run-3"):
                        raise AssertionError("over-capacity reservation was admitted")
                assert capacity.value.code == "director_help_capacity_exceeded"
                assert gate.active_count() == 2

        assert gate.active_count() == 0

    asyncio.run(exercise())


def test_gate_releases_after_success_and_exception() -> None:
    class ExpectedFailure(RuntimeError):
        pass

    async def exercise() -> None:
        gate = DirectorHelpCallGate(1)

        async with gate.reserve("run-1"):
            assert gate.active_count() == 1
        assert gate.active_count() == 0

        with pytest.raises(ExpectedFailure):
            async with gate.reserve("run-1"):
                raise ExpectedFailure
        assert gate.active_count() == 0

        async with gate.reserve("run-1"):
            assert gate.active_count() == 1

    asyncio.run(exercise())


def test_gate_releases_after_task_cancellation() -> None:
    async def exercise() -> None:
        gate = DirectorHelpCallGate(1)
        started = asyncio.Event()

        async def hold_slot() -> None:
            async with gate.reserve("run-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold_slot())
        await started.wait()
        assert gate.active_count() == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate.active_count() == 0

        async with gate.reserve("run-1"):
            assert gate.active_count() == 1

    asyncio.run(exercise())


def test_create_app_owns_configured_director_help_gate(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            db_path=tmp_path / "director-help-gate.sqlite3",
            module_asset_root=tmp_path / "module-assets",
            director_help_max_concurrency=3,
        )
    )

    gate = app.state.director_help_call_gate
    assert isinstance(gate, DirectorHelpCallGate)
    assert gate.max_concurrency == 3
    assert gate.active_count() == 0
