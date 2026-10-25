import asyncio
import threading

import pytest
from fastapi import BackgroundTasks
from task_helpers import SampleHandler, command, records

from app.models import RuntimeState, Task
from app.services.tasks import register_submission


def test_exception_sets_failed_and_releases_known_idle_slot(engine):
    service, ids, handler = engine
    handler.error = RuntimeError("must not expose secret prompt")
    submitted = service.submit(command(*ids))
    asyncio.run(service.runner.run(submitted.task.id))
    result = service.get(submitted.task.id)
    assert result.status == "FAILED" and "secret" not in result.error_message
    with service.database.session_scope() as session:
        assert session.get(RuntimeState, 1).state == "IDLE"
        assert session.get(Task, result.id).finished_at is not None
    assert service.submit(command(*ids)).created


def test_runner_compare_and_swap_only_executes_once(engine):
    service, ids, handler = engine
    submitted = service.submit(command(*ids))

    async def run():
        await asyncio.gather(
            service.runner.run(submitted.task.id), service.runner.run(submitted.task.id)
        )

    asyncio.run(run())
    assert handler.calls == [submitted.task.id]
    assert service.get(submitted.task.id).status == "DONE"


@pytest.mark.parametrize("bad_result", [False, True])
def test_outcome_publication_is_atomic_and_never_changes_selection(engine, bad_result):
    from PIL import Image
    from sqlalchemy import select

    from app.models import Asset, ShotSelection
    from app.services.tasks import PreparedAsset, TaskOutcome

    service, ids, _ = engine

    class ImageHandler(SampleHandler):
        async def execute(self, context):
            temporary = service.storage.new_temp(".png")
            Image.new("RGB", (32, 32), "#88aa77").save(temporary, "PNG")
            published = service.storage.publish_file(temporary, ids[0])
            asset = PreparedAsset(
                type="image",
                file_path=published.path,
                sha256=published.sha256,
                size_bytes=published.size_bytes,
                mime_type="image/png",
                name="原创图片",
                width=32,
                height=32,
            )
            return TaskOutcome(
                prepared_asset=asset,
                result_json={"value": object()} if bad_result else None,
            )

    service.handlers["IMAGE"] = ImageHandler()
    submitted = service.submit(command(*ids, kind="IMAGE"))
    asyncio.run(service.runner.run(submitted.task.id))
    with service.database.session_scope() as session:
        assets = session.scalars(select(Asset)).all()
        task = session.get(Task, submitted.task.id)
        assert task.status == ("FAILED" if bad_result else "DONE")
        assert len(assets) == (0 if bad_result else 1)
        assert session.get(ShotSelection, ids[2]).first_frame_asset_id is None
        assert session.get(RuntimeState, 1).state == "IDLE"
        if not bad_result:
            assert assets[0].source_task_id == task.id
            assert task.output_asset_id == assets[0].id


def test_heartbeat_updates_without_inventing_progress(engine):

    service, ids, handler = engine
    service.policy.heartbeat_seconds = 0.01
    handler.hold = threading.Event()
    task = service.submit(command(*ids)).task

    async def run():
        running = asyncio.create_task(service.runner.run(task.id))
        while not handler.started.is_set():
            await asyncio.sleep(0.002)
        before = service.get(task.id).heartbeat_at
        await asyncio.sleep(0.05)
        current = service.get(task.id)
        assert current.heartbeat_at > before
        assert current.progress is None
        handler.hold.set()
        await running

    asyncio.run(run())


def test_jobs_routes_have_no_production_fake_submit(client):
    assert client.post("/api/v1/jobs", json={"kind": "IMAGE"}).status_code == 404
    active = client.get("/api/v1/jobs/active")
    assert active.status_code == 200
    assert active.json()["task"] is None
    assert active.json()["runtime_state"]["state"] == "IDLE"
    assert client.get("/api/v1/jobs/99999").status_code == 404


def test_response_returns_before_model_finishes(tmp_path):
    # 真TCP；TestClient会等待BackgroundTasks，不能用于这个时序断言。
    import json
    import socket
    import time
    from urllib.request import Request, urlopen

    import uvicorn
    from fastapi import Request as FastAPIRequest

    from app.config import AppSettings
    from app.main import create_app

    hold = threading.Event()
    handler = SampleHandler(hold=hold)
    app = create_app(
        AppSettings(data_dir=tmp_path / "tcp"),
        task_handlers={"OPTIMIZE_PROMPTS": handler},
    )

    @app.post("/api/v1/test-only-submit", status_code=202)
    def submit(background: BackgroundTasks, request: FastAPIRequest):
        ids = records(request.app.state.database)
        submitted = request.app.state.task_service.submit(command(*ids))
        register_submission(background, submitted)
        return {"job_id": submitted.task.id}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.005)
        origin = f"http://127.0.0.1:{port}"
        with urlopen(origin + "/api/v1/bootstrap", timeout=2) as response:
            token = json.load(response)["token"]
        req = Request(
            origin + "/api/v1/test-only-submit",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "X-Local-Token": token},
        )
        with urlopen(req, timeout=2) as response:
            assert response.status == 202
            job_id = json.load(response)["job_id"]
        assert not hold.is_set()
        with urlopen(origin + f"/api/v1/jobs/{job_id}", timeout=2) as response:
            assert json.load(response)["status"] in ("PENDING", "RUNNING")
    finally:
        hold.set()
        server.should_exit = True
        thread.join(timeout=3)
        sock.close()
        assert not thread.is_alive()
