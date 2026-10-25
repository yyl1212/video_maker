import asyncio
import time

import pytest
from task_helpers import SampleHandler, command

from app.errors import AppError
from app.services.recovery import RecoveryService
from app.services.tasks import TaskOutcome


def test_deadline_does_not_reset_on_progress(engine):
    service, ids, _ = engine
    service.policy.budgets["OPTIMIZE_PROMPTS"] = 0.08

    class ProgressHandler(SampleHandler):
        async def execute(self, context):
            while True:
                await context.report("持续进度", 0.2)
                await asyncio.sleep(0.005)

    service.handlers["OPTIMIZE_PROMPTS"] = ProgressHandler()
    task = service.submit(command(*ids)).task
    start = time.monotonic()
    asyncio.run(service.runner.run(task.id))
    assert time.monotonic() - start < 1
    assert service.get(task.id).error_code == "DEADLINE_EXCEEDED"
    assert service.active().runtime_state.state == "IDLE"
    assert service.get(task.id).deadline_at is not None


def test_remote_timeout_requires_explicit_reset(engine):
    service, ids, _ = engine
    service.policy.budgets["OPTIMIZE_PROMPTS"] = 0.04

    class RemoteHandler(SampleHandler):
        async def execute(self, context):
            await context.mark_remote_started("mlx")
            await asyncio.sleep(30)

    service.handlers["OPTIMIZE_PROMPTS"] = RemoteHandler()
    task = service.submit(command(*ids)).task
    asyncio.run(service.runner.run(task.id))
    assert service.get(task.id).status == "FAILED"
    assert service.active().runtime_state.state == "NEEDS_RESET"


def test_live_runner_cannot_be_released_by_reset(engine):
    service, ids, _ = engine
    service.policy.budgets["OPTIMIZE_PROMPTS"] = 0.03
    service.policy.cancel_grace_seconds = 0.02
    allow_stop = asyncio.Event()

    class SlowCancellation(SampleHandler):
        async def execute(self, context):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await allow_stop.wait()
                return TaskOutcome(result_json={"summary": "超时后的结果不能发布"})

    service.handlers["OPTIMIZE_PROMPTS"] = SlowCancellation()
    task = service.submit(command(*ids)).task

    async def run():
        await service.runner.run(task.id)
        state = service.active().runtime_state
        assert state.state == "NEEDS_RESET"
        with pytest.raises(AppError) as error:
            RecoveryService(service).confirm_reset(
                state.version, state.affected_backend
            )
        assert error.value.status == 409
        allow_stop.set()
        await asyncio.sleep(0.04)
        assert service.get(task.id).status == "FAILED"
        assert (
            RecoveryService(service)
            .confirm_reset(state.version, state.affected_backend)
            .state
            == "IDLE"
        )

    asyncio.run(run())


def test_owned_ffmpeg_launcher_completes_with_proven_identity(engine):
    service, ids, _ = engine

    class LocalHandler(SampleHandler):
        async def execute(self, context):
            result = await context.run_process(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=32x32:r=24",
                    "-t",
                    "0.1",
                    "-f",
                    "null",
                    "-",
                ]
            )
            assert result.returncode == 0 and result.stderr_path.read_bytes() == b""
            return TaskOutcome(result_json={"summary": "原创色块验证完成"})

    service.handlers["OPTIMIZE_PROMPTS"] = LocalHandler()
    task = service.submit(command(*ids)).task
    asyncio.run(service.runner.run(task.id))
    assert service.get(task.id).status == "DONE"


def test_owned_subprocess_is_stopped_at_task_deadline(engine):
    import os
    import sys

    service, ids, _ = engine
    service.policy.budgets["OPTIMIZE_PROMPTS"] = 0.4

    class ProcessHandler(SampleHandler):
        async def execute(self, context):
            await context.run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
            return TaskOutcome(result_json={"summary": "不应完成"})

    service.handlers["OPTIMIZE_PROMPTS"] = ProcessHandler()
    task = service.submit(command(*ids)).task
    asyncio.run(service.runner.run(task.id))
    from app.models import Task

    with service.database.session_scope() as session:
        local = session.get(Task, task.id).execution_json["local"]
    assert local["finished"], (
        local,
        service.get(task.id).error_code,
        service.get(task.id).error_message,
    )
    with pytest.raises(ProcessLookupError):
        os.kill(local["identity"]["pid"], 0)
    assert service.active().runtime_state.state == "IDLE"
