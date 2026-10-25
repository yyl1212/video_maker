from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select
from test_assets_http import upload_image
from test_shots import create_shot, workspace

from app.errors import BackendError
from app.models import Asset, ModelSettings, RuntimeState, ShotSelection
from app.services.recovery import RecoveryService

MODEL = "flux_2_klein_9b_kv_i8x.ckpt"


class FakeBackend:
    def __init__(self):
        self.requests = []
        self.error = None
        self.before_result = None

    async def generate(self, request, on_progress, *, on_dispatch, on_terminal, io):
        from app.clients.drawthings import ImageResult

        await on_dispatch()
        self.requests.append(request)
        await on_progress("采样", None)
        if self.error:
            raise self.error
        await on_terminal()
        if self.before_result:
            await io(self.before_result)
        await io(Image.new("RGB", (128, 192), "olive").save, request.output_path, "PNG")
        return ImageResult(
            request.output_path, 128, 192, {"actual_config": {"seed": 0}}
        )


def prepare(client):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    database = client.app.state.database
    with database.session_scope(write=True) as session:
        record = session.get(ModelSettings, 1)
        record.image_settings = {
            **record.image_settings,
            "model": MODEL,
            "width": 128,
            "height": 192,
            "seed": 0,
        }
    refs = [upload_image(client, project["id"]).json() for _ in range(2)]
    shot = client.put(
        f"/api/v1/shots/{shot['id']}/references",
        json={
            "expected_version": shot["version"],
            "references": [
                {"asset_id": refs[1]["id"], "role": "scene"},
                {"asset_id": refs[0]["id"], "role": "character"},
            ],
        },
    ).json()
    episode = client.get(f"/api/v1/episodes/{episode['id']}").json()
    return project, episode, shot, refs


def body(project, episode, shot):
    return {
        "idempotency_key": str(uuid4()),
        "expected_project_version": project["version"],
        "expected_episode_version": episode["version"],
        "expected_shot_version": shot["version"],
        "expected_image_input_hash": shot["image_input_hash"],
    }


def install(client):
    from app.services.generation import ImageGenerationHandler

    fake = FakeBackend()
    service = client.app.state.task_service
    service.handlers["IMAGE"] = ImageGenerationHandler(
        service.database, service.storage, fake
    )
    return service, fake


def submit(client, shot, data):
    return client.post(f"/api/v1/shots/{shot['id']}/image-jobs", json=data)


def test_image_job_freezes_references_and_publishes_history_without_selection(client):
    project, episode, shot, refs = prepare(client)
    service, fake = install(client)
    data = body(project, episode, shot)
    response = submit(client, shot, data)
    assert response.status_code == 202, response.text
    task = client.get(response.json()["poll_url"]).json()
    assert task["status"] == "DONE", task
    request = fake.requests[0]
    assert [p.name for p in request.references] == [
        f"00-{refs[1]['id']}.png",
        f"01-{refs[0]['id']}.png",
    ]
    assert all("task-inputs" in str(p) for p in request.references)
    assert "Picture 1" in request.prompt and request.settings.seed == 0
    with service.database.session_scope() as session:
        asset = session.get(Asset, task["output_asset_id"])
        assert asset.source_task_id == task["id"] and asset.shot_id == shot["id"]
        assert asset.tags == ["first_frame"]
        assert [r["asset_id"] for r in asset.metadata_json["references"]] == [
            refs[1]["id"],
            refs[0]["id"],
        ]
        assert session.get(ShotSelection, shot["id"]).first_frame_asset_id is None
        assert session.get(RuntimeState, 1).state == "IDLE"
    again = submit(client, shot, data)
    assert again.json()["job_id"] == task["id"] and len(fake.requests) == 1


def test_regeneration_preserves_current_frame_and_different_paths(client):
    project, episode, shot, _ = prepare(client)
    service, _ = install(client)
    first = submit(client, shot, body(project, episode, shot)).json()["job_id"]
    frame = service.get(first).output_asset_id
    shot = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": shot["version"],
            "selection_version": 1,
            "first_frame_asset_id": frame,
        },
    ).json()
    second = submit(client, shot, body(project, episode, shot)).json()["job_id"]
    with service.database.session_scope() as session:
        one, two = (
            session.get(Asset, frame),
            session.get(Asset, service.get(second).output_asset_id),
        )
        assert one.file_path != two.file_path
        assert Path(one.file_path).read_bytes() == Path(two.file_path).read_bytes()
        assert session.get(ShotSelection, shot["id"]).first_frame_asset_id == frame


def test_edit_during_generation_keeps_old_input_as_stale_history(client):
    project, episode, shot, _ = prepare(client)
    service, fake = install(client)

    def edit():
        from app.models import Shot

        with service.database.session_scope(write=True) as session:
            record = session.get(Shot, shot["id"])
            record.image_prompt = "changed while rendering"
            record.version += 1

    fake.before_result = edit
    response = submit(client, shot, body(project, episode, shot))
    task = service.get(response.json()["job_id"])
    latest = client.get(f"/api/v1/shots/{shot['id']}").json()
    assert task.status == "DONE"
    adopt = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": latest["version"],
            "selection_version": 1,
            "first_frame_asset_id": task.output_asset_id,
        },
    )
    assert adopt.status_code == 409


@pytest.mark.parametrize("uncertain", [False, True])
def test_failure_releases_or_closes_runtime_according_to_remote_state(
    client, uncertain
):
    project, episode, shot, _ = prepare(client)
    service, fake = install(client)
    fake.error = BackendError("DRAWTHINGS_TIMEOUT", "本机模型失败", uncertain)
    # 已发出RPC时即使错误误称确定，未收到正常终止证据也须封闭。
    response = submit(client, shot, body(project, episode, shot))
    task = service.get(response.json()["job_id"])
    assert task.status == "FAILED"
    assert service.active().runtime_state.state == "NEEDS_RESET"
    assert service.active().runtime_state.affected_backend == "drawthings"


def test_changed_defaults_are_rejected_before_claim(client):
    project, episode, shot, _ = prepare(client)
    service, fake = install(client)
    with service.database.session_scope(write=True) as session:
        settings = session.get(ModelSettings, 1)
        settings.image_settings = {**settings.image_settings, "seed": 44}
    response = submit(client, shot, body(project, episode, shot))
    assert response.status_code == 409
    assert not fake.requests and service.active().runtime_state.state == "IDLE"


def test_pending_image_blocks_all_new_generation_and_same_key_not_redispatched(client):
    project, episode, shot, _ = prepare(client)
    service, fake = install(client)
    # 截住注册的后台执行，保持真实PENDING占位验证路由。
    from unittest.mock import patch

    with patch.object(service.runner, "run", new=async_noop):
        response = submit(client, shot, body(project, episode, shot))
    assert response.status_code == 202
    assert submit(client, shot, body(project, episode, shot)).status_code == 429
    assert not fake.requests


async def async_noop(*args):
    pass


def test_publish_then_commit_failure_quarantines_only_orphans_on_restart(client):
    project, episode, shot, refs = prepare(client)
    service, fake = install(client)
    # 图片已发布且Asset完成flush后，模拟事务提交失败。
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def before_flush(session, *_):
        if any(isinstance(r, Asset) and r.source_task_id for r in session.new):
            session.info["generated_asset"] = True

    def before_commit(session):
        if session.info.get("generated_asset"):
            raise OSError("simulated asset commit failure")

    event.listen(Session, "before_flush", before_flush)
    event.listen(Session, "before_commit", before_commit)
    try:
        response = submit(client, shot, body(project, episode, shot))
    finally:
        event.remove(Session, "before_flush", before_flush)
        event.remove(Session, "before_commit", before_commit)
    assert service.get(response.json()["job_id"]).status != "DONE"
    with service.database.session_scope() as session:
        registered = {a.file_path for a in session.scalars(select(Asset))}
        registered.update(
            a.thumbnail_path for a in session.scalars(select(Asset)) if a.thumbnail_path
        )
    orphans = [
        p for p in service.storage.media.rglob("*.png") if str(p) not in registered
    ]
    assert orphans
    RecoveryService(service).startup()
    service.storage.quarantine_orphans(registered)
    assert all(not p.exists() for p in orphans)
    assert all(Path(r["file_path"]).exists() for r in refs)


def test_known_image_result_failure_marks_failed_and_runtime_idle(client):
    project, episode, shot, _ = prepare(client)
    service, fake = install(client)

    class EmptyBackend:
        async def generate(self, request, on_progress, *, on_dispatch, on_terminal, io):
            await on_dispatch()
            await on_terminal()
            raise BackendError("IMAGE_EMPTY_RESULT", "未返回图片", False)

    service.handlers["IMAGE"].backend = EmptyBackend()
    response = submit(client, shot, body(project, episode, shot))
    task = service.get(response.json()["job_id"])
    assert task.status == "FAILED" and task.error_code == "IMAGE_EMPTY_RESULT"
    assert service.active().runtime_state.state == "IDLE"
    assert task.output_asset_id is None


def test_connection_probe_does_not_release_needs_reset_runtime(client, monkeypatch):
    from app.clients.drawthings import DrawThingsBackend, ImageConnectionRead

    service = client.app.state.task_service
    with service.database.session_scope(write=True) as session:
        runtime = session.get(RuntimeState, 1)
        runtime.state, runtime.affected_backend = "NEEDS_RESET", "drawthings"

    async def probe(self, settings):
        return ImageConnectionRead(models=[])

    monkeypatch.setattr(DrawThingsBackend, "test_connection", probe)
    settings = client.get("/api/v1/settings").json()["image"]
    response = client.post("/api/v1/settings/test-connection", json={"image": settings})
    assert response.status_code == 200
    assert service.active().runtime_state.state == "NEEDS_RESET"


def test_image_route_rejects_path_injection_and_deleted_shot(client):
    project, episode, shot, _ = prepare(client)
    install(client)
    request = body(project, episode, shot)
    assert (
        submit(
            client, shot, {**request, "references": ["/private/path.png"]}
        ).status_code
        == 422
    )
    client.request(
        "DELETE",
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"]},
    )
    assert submit(client, shot, request).status_code == 409
    request["expected_episode_version"] = client.get(
        f"/api/v1/episodes/{episode['id']}"
    ).json()["version"]
    assert submit(client, shot, request).status_code == 404
