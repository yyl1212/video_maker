import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from task_helpers import command

from app.config import AppSettings
from app.errors import AppError
from app.main import create_app
from app.models import RuntimeState, Task
from app.models.base import utc_now
from app.services.recovery import RecoveryService


def test_second_backend_instance_cannot_open_workspace(tmp_path):
    config = AppSettings(data_dir=tmp_path / "locked")
    with TestClient(create_app(config), base_url="http://127.0.0.1:8000") as first:
        with pytest.raises(AppError) as error:
            with TestClient(create_app(config)):
                pass
        assert error.value.code == "WORKSPACE_IN_USE"
        assert first.get("/api/v1/health").status_code == 200
    # 锁释放后可以正常启动，不删除锁文件造成inode竞争。
    with TestClient(create_app(config), base_url="http://127.0.0.1:8000") as restarted:
        assert restarted.get("/api/v1/health").status_code == 200


def test_restart_fails_pending_without_replay(engine):
    service, ids, handler = engine
    task = service.submit(command(*ids)).task
    RecoveryService(service).startup()
    assert service.get(task.id).status == "FAILED"
    assert service.get(task.id).error_code == "APP_INTERRUPTED"
    assert service.active().runtime_state.state == "IDLE"
    assert handler.calls == []


@pytest.mark.parametrize("finished", [False, True])
def test_restart_after_remote_dispatch_requires_reset(engine, finished):
    service, ids, handler = engine
    task = service.submit(command(*ids)).task
    with service.database.session_scope(write=True) as session:
        row = session.get(Task, task.id)
        row.status, row.execution_owner = "RUNNING", "old-instance"
        row.execution_json = {
            "remote": {"backend": "drawthings", "started": True, "finished": finished}
        }
    recovery = RecoveryService(service)
    recovery.startup()
    assert service.get(task.id).status == "FAILED" and handler.calls == []
    runtime = service.active().runtime_state
    assert runtime.state == ("IDLE" if finished else "NEEDS_RESET")
    if not finished:
        with pytest.raises(AppError) as error:
            service.submit(command(*ids))
        assert error.value.status == 503
        with pytest.raises(AppError):
            recovery.confirm_reset(runtime.version, "mlx")
        assert recovery.confirm_reset(runtime.version, "drawthings").state == "IDLE"


def test_pending_watchdog_and_stale_heartbeat_does_not_release_live_runner(engine):
    service, ids, _ = engine
    pending = service.submit(command(*ids)).task
    with service.database.session_scope(write=True) as session:
        session.get(Task, pending.id).created_at = utc_now() - timedelta(seconds=31)
    recovery = RecoveryService(service)
    recovery.inspect_pending()
    assert service.get(pending.id).error_code == "BACKGROUND_NOT_STARTED"
    running = service.submit(command(*ids)).task
    service.claim(running.id)
    with service.database.session_scope(write=True) as session:
        session.get(Task, running.id).heartbeat_at = utc_now() - timedelta(seconds=31)
    recovery.inspect_pending()
    assert service.get(running.id).status == "RUNNING"
    assert service.active().runtime_state.state == "ACTIVE"
    assert "心跳" in service.active().runtime_state.reason
    with pytest.raises(AppError) as error:
        service.submit(command(*ids))
    assert error.value.status == 429
    service.finish(running.id, error_code="TEST_STOP", error_message="测试结束")


def test_local_ffmpeg_identity_not_pid_only(engine):
    import subprocess
    import sys

    from app.services.recovery import ProcessGuard

    service, ids, _ = engine
    job = service.submit(command(*ids)).task
    directory = service.storage.root / "jobs" / str(job.id)
    directory.mkdir(parents=True)
    token = "self-made-token"
    (directory / ".process-token").write_text(token)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=directory,
        start_new_session=True,
    )
    guard = ProcessGuard(service.storage)
    try:
        identity = guard.capture(process.pid, job.id, token, directory)
        identity["create_time"] -= 100
        with service.database.session_scope(write=True) as session:
            row = session.get(Task, job.id)
            row.status = "RUNNING"
            row.execution_json = {
                "local": {"started": True, "finished": False, "identity": identity}
            }
        RecoveryService(service).startup()
        assert process.poll() is None
        assert service.active().runtime_state.state == "NEEDS_RESET"
        assert service.active().runtime_state.affected_backend == "ffmpeg"
    finally:
        process.kill()
        process.wait(timeout=2)


def test_terminal_database_failure_stays_closed_and_metadata_can_recover(
    engine, monkeypatch
):
    service, ids, handler = engine
    handler.error = RuntimeError("自制错误")
    original = service.finish
    unavailable = True

    def fail_write(*args, **kwargs):
        if unavailable:
            raise AppError(503, "DATABASE_UNAVAILABLE", "数据库暂不可用")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "finish", fail_write)
    task = service.submit(command(*ids)).task
    asyncio.run(service.runner.run(task.id))
    assert service.get(task.id).status == "RUNNING"
    with pytest.raises(AppError) as error:
        service.submit(command(*ids))
    assert error.value.status == 503
    unavailable = False
    RecoveryService(service).inspect_pending()
    assert service.get(task.id).status == "FAILED"
    assert service.active().runtime_state.state == "IDLE"
    assert handler.calls == [task.id]
    assert service.submit(command(*ids)).created


def test_reset_http_requires_explicit_confirmation_and_version(client):
    with client.app.state.database.session_scope(write=True) as session:
        state = session.get(RuntimeState, 1)
        state.state, state.affected_backend, state.reason = (
            "NEEDS_RESET",
            "mlx",
            "模型执行未知",
        )
    result = client.post(
        "/api/v1/runtime/reset",
        json={"expected_version": 1, "backend": "mlx", "confirmed_stopped": False},
    )
    assert result.status_code == 422
    assert (
        client.post(
            "/api/v1/runtime/reset",
            json={"expected_version": 99, "backend": "mlx", "confirmed_stopped": True},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/runtime/reset",
            json={"expected_version": 1, "backend": "mlx", "confirmed_stopped": True},
        ).json()["state"]
        == "IDLE"
    )
