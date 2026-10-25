from pathlib import Path
from uuid import uuid4

import pytest
from test_assets_http import make_project, upload_image
from test_shots import create_shot, workspace

from app.errors import AppError
from app.models import Asset, Episode, Shot, ShotSelection, Task
from app.schemas.tasks import TaskSubmitCommand


def timeline(client, video_file):
    project, episode = workspace(client)
    image = upload_image(client, project["id"]).json()
    clips = []
    for i in range(2):
        with video_file.open("rb") as source:
            clip = client.post(
                f"/api/v1/projects/{project['id']}/assets",
                files={"file": (f"clip{i}.mp4", source, "video/mp4")},
            ).json()
        shot = create_shot(
            client, episode["id"], video_prompt=f"Original movement {i}"
        ).json()
        shot = client.put(
            f"/api/v1/shots/{shot['id']}/selection",
            json={
                "shot_version": shot["version"],
                "selection_version": shot["selection"]["version"],
                "first_frame_asset_id": image["id"],
                "confirm_uploaded": True,
                "expected_image_input_hash": shot["image_input_hash"],
            },
        ).json()
        shot = client.put(
            f"/api/v1/shots/{shot['id']}/selection",
            json={
                "shot_version": shot["version"],
                "selection_version": shot["selection"]["version"],
                "selected_video_asset_id": clip["id"],
                "confirm_uploaded": True,
                "expected_video_input_hash": shot["video_input_hash"],
            },
        ).json()
        clips.append((shot, clip))
    return project, episode, clips


def preview(client, episode):
    response = client.get(f"/api/v1/episodes/{episode['id']}/export-preview")
    assert response.status_code == 200, response.text
    return response.json()


def body(data, *, confirm=False, profile="portrait"):
    return {
        "idempotency_key": str(uuid4()),
        "expected_project_version": data["project_version"],
        "expected_episode_version": data["episode_version"],
        "output_profile": profile,
        "clips": [
            {
                "shot_id": r["shot_id"],
                "review_token": r["review_token"],
                "confirm_stale": confirm,
            }
            for r in data["clips"]
        ],
    }


def command(data, request):
    return TaskSubmitCommand(
        project_id=data["project_id"],
        episode_id=data["episode_id"],
        kind="EXPORT",
        idempotency_key=request["idempotency_key"],
        expected_project_version=request["expected_project_version"],
        expected_episode_version=request["expected_episode_version"],
        parameters={k: request[k] for k in ("output_profile", "clips")},
    )


def test_preview_defaults_to_current_order_and_selected_clips(client, video_file):
    project, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    assert [r["shot_id"] for r in data["clips"]] == [s["id"] for s, a in clips]
    assert [r["asset"]["id"] for r in data["clips"]] == [a["id"] for s, a in clips]
    assert all(r["available"] and not r["stale"] for r in data["clips"])
    assert all(len(r["review_token"]) > 64 for r in data["clips"])


def test_submission_snapshot_keeps_order_selection_and_acknowledgements(
    client, video_file
):
    project, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    request = body(data)
    request["clips"].reverse()
    service = client.app.state.task_service
    accepted = service.submit(command(data, request))
    with service.database.session_scope(write=True) as session:
        one = session.get(Shot, clips[0][0]["id"])
        one.order_index = 20
        one.video_prompt = "new motion"
        one.version += 1
        selected = session.get(ShotSelection, one.id)
        selected.selected_video_asset_id = clips[1][1]["id"]
        selected.version += 1
        session.get(Episode, episode["id"]).version += 1
    with service.database.session_scope() as session:
        task = session.get(Task, accepted.task.id)
        assert [c["shot_id"] for c in task.input_snapshot["data"]["clips"]] == [
            s["id"] for s, a in clips
        ][::-1]
        assert [c["asset_id"] for c in task.input_snapshot["data"]["clips"]] == [
            a["id"] for s, a in clips
        ][::-1]
        assert all(
            not c["confirmed_stale"] for c in task.input_snapshot["data"]["clips"]
        )
    assert service.submit(command(data, request)).task.id == accepted.task.id


def test_missing_clips_are_listed_and_can_only_be_explicitly_excluded(
    client, video_file
):
    project, episode, clips = timeline(client, video_file)
    with client.app.state.database.session_scope(write=True) as session:
        session.get(ShotSelection, clips[0][0]["id"]).selected_video_asset_id = None
    data = preview(client, episode)
    assert not data["clips"][0]["available"]
    request = body(data, confirm=True)
    response = client.post(
        f"/api/v1/episodes/{episode['id']}/export-jobs", json=request
    )
    assert response.status_code == 422 and "镜头" in response.json()["error"]["message"]
    request["clips"] = request["clips"][1:]
    assert client.app.state.task_service.submit(command(data, request)).created


def test_stale_clip_requires_bound_confirmation_and_global_input_changes_invalidate_it(
    client, video_file
):
    project, episode, clips = timeline(client, video_file)
    service = client.app.state.task_service
    with service.database.session_scope(write=True) as session:
        session.get(Shot, clips[0][0]["id"]).video_prompt = "changed motion"
    data = preview(client, episode)
    assert data["clips"][0]["stale"]
    with pytest.raises(AppError) as error:
        service.submit(command(data, body(data)))
    assert error.value.code == "EXPORT_NOT_READY"
    request = body(data, confirm=True)
    # 全局生图设置变化不会递增Shot版本，确认仍须失效。
    from app.models import ModelSettings

    with service.database.session_scope(write=True) as session:
        settings = session.get(ModelSettings, 1)
        settings.image_settings = {**settings.image_settings, "seed": 99}
    with pytest.raises(AppError) as error:
        service.submit(command(data, request))
    assert error.value.code == "EXPORT_TIMELINE_CHANGED"
    latest = preview(client, episode)
    accepted = service.submit(command(latest, body(latest, confirm=True)))
    with service.database.session_scope() as session:
        task = session.get(Task, accepted.task.id)
        assert task.input_snapshot["data"]["clips"][0]["confirmed_stale"] is True
        assert task.input_snapshot["data"]["clips"][0]["stale"] is True


def test_foreign_shots_foreign_asset_and_corrupt_file_are_rejected(client, video_file):
    project, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    service = client.app.state.task_service
    other = make_project(client, "Other original")
    with video_file.open("rb") as source:
        foreign = client.post(
            f"/api/v1/projects/{other['id']}/assets",
            files={"file": ("other.mp4", source, "video/mp4")},
        ).json()
    with service.database.session_scope(write=True) as session:
        session.get(ShotSelection, clips[0][0]["id"]).selected_video_asset_id = foreign[
            "id"
        ]
    row = preview(client, episode)
    assert not row["clips"][0]["available"]
    with pytest.raises(AppError):
        service.submit(command(row, body(row, confirm=True)))
    with service.database.session_scope(write=True) as session:
        session.get(ShotSelection, clips[0][0]["id"]).selected_video_asset_id = clips[
            0
        ][1]["id"]
    path = Path(clips[0][1]["file_path"])
    path.chmod(0o644)
    path.write_bytes(b"changed")
    row = preview(client, episode)
    assert not row["clips"][0]["available"]
    with pytest.raises(AppError):
        service.submit(command(row, body(row, confirm=True)))
    wrong = body(data)
    wrong["clips"][0]["shot_id"] = 999999
    with pytest.raises(AppError):
        service.submit(command(data, wrong))


def test_empty_duplicate_and_path_parameters_rejected_by_http(client, video_file):
    project, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    url = f"/api/v1/episodes/{episode['id']}/export-jobs"
    request = body(data)
    assert client.post(url, json={**request, "clips": []}).status_code == 422
    assert (
        client.post(
            url, json={**request, "clips": [request["clips"][0]] * 2}
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={**request, "output_path": "/private/arbitrary.mp4"}
        ).status_code
        == 422
    )


def test_export_files_manifest_download_and_repeated_exports_are_independent(
    client, video_file
):
    project, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    url = f"/api/v1/episodes/{episode['id']}/export-jobs"
    first = client.post(url, json=body(data))
    assert first.status_code == 202, first.text
    job = client.get(first.json()["poll_url"]).json()
    assert job["status"] == "DONE", job
    exports = client.get(f"/api/v1/episodes/{episode['id']}/exports").json()
    asset = exports[0]
    assert asset["id"] == job["output_asset_id"] and asset["tags"] == ["final_video"]
    manifest = client.get(
        f"/api/v1/assets/{asset['id']}/manifest?project_id={project['id']}"
    )
    assert manifest.status_code == 200, manifest.text
    payload = manifest.json()
    assert [c["asset_id"] for c in payload["clips"]] == [a["id"] for s, a in clips]
    assert payload["output"]["sha256"] == asset["sha256"]
    second = client.post(url, json=body(data))
    assert second.status_code == 202
    second_job = client.get(second.json()["poll_url"]).json()
    assert second_job["status"] == "DONE", second_job
    other = client.get(f"/api/v1/episodes/{episode['id']}/exports").json()[0]
    assert (
        asset["file_path"] != other["file_path"]
        and asset["metadata_json"]["manifest_path"]
        != other["metadata_json"]["manifest_path"]
    )
    assert Path(asset["file_path"]).exists()
    assert (
        client.get(f"/api/v1/assets/{asset['id']}/manifest?project_id=999").status_code
        == 404
    )
    with client.app.state.database.session_scope() as session:
        assert (
            session.get(ShotSelection, clips[0][0]["id"]).selected_video_asset_id
            == clips[0][1]["id"]
        )


def test_manifest_survives_media_scan_and_rejects_tampering_or_arbitrary_path(
    client, video_file
):
    from sqlalchemy import select

    project, episode, _ = timeline(client, video_file)
    response = client.post(
        f"/api/v1/episodes/{episode['id']}/export-jobs",
        json=body(preview(client, episode)),
    )
    job = client.get(response.json()["poll_url"]).json()
    assert job["status"] == "DONE", job
    service = client.app.state.task_service
    with service.database.session_scope() as session:
        assets = list(session.scalars(select(Asset)))
        asset = session.get(Asset, job["output_asset_id"])
        path = Path(asset.metadata_json["manifest_path"])
        registered = {p for a in assets for p in (a.file_path, a.thumbnail_path) if p}
    assert service.storage.quarantine_orphans(registered) == 0
    url = f"/api/v1/assets/{job['output_asset_id']}/manifest?project_id={project['id']}"
    assert client.get(url).status_code == 200 and path.exists()
    original = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(b"{}")
    assert client.get(url).status_code == 409
    path.write_bytes(original)
    with service.database.session_scope(write=True) as session:
        asset = session.get(Asset, job["output_asset_id"])
        asset.metadata_json = {
            **asset.metadata_json,
            "manifest_path": str(path.parent / "other.json"),
        }
    assert client.get(url).status_code == 409


def test_restart_invalidates_unaccepted_preview_but_preserves_accepted_idempotency(
    client, video_file
):
    from app.services.exports import ExportHandler

    _, episode, _ = timeline(client, video_file)
    data = preview(client, episode)
    request = body(data)
    service = client.app.state.task_service
    accepted = service.submit(command(data, request))
    service.handlers["EXPORT"] = ExportHandler(service.database, service.storage)
    assert service.submit(command(data, request)).task.id == accepted.task.id
    changed = {**request, "idempotency_key": str(uuid4())}
    # 已占槽时先返回429；结束旧任务后验证重启令牌的明确拒绝。
    service.fail_pending(accepted.task.id, "TEST_STOP", "test")
    with pytest.raises(AppError) as error:
        service.submit(command(data, changed))
    assert error.value.code == "EXPORT_TIMELINE_CHANGED"


def test_published_export_with_failed_commit_never_becomes_done_or_loses_sources(
    client, video_file
):
    from sqlalchemy import event, select
    from sqlalchemy.orm import Session

    from app.services.recovery import RecoveryService

    project, episode, clips = timeline(client, video_file)
    service = client.app.state.task_service

    def flush(session, *_):
        if any(isinstance(r, Asset) and r.source_task_id for r in session.new):
            session.info["export_output"] = True

    def commit(session):
        if session.info.get("export_output"):
            raise OSError("simulated commit failure")

    event.listen(Session, "before_flush", flush)
    event.listen(Session, "before_commit", commit)
    try:
        response = client.post(
            f"/api/v1/episodes/{episode['id']}/export-jobs",
            json=body(preview(client, episode)),
        )
    finally:
        event.remove(Session, "before_flush", flush)
        event.remove(Session, "before_commit", commit)
    task = service.get(response.json()["job_id"])
    assert task.status != "DONE" and task.output_asset_id is None
    assert not client.get(f"/api/v1/episodes/{episode['id']}/exports").json()
    manifest = service.storage.root / "jobs" / str(task.id) / "manifest.json"
    assert manifest.exists()
    with service.database.session_scope() as session:
        registered = {
            p
            for a in session.scalars(select(Asset))
            for p in (a.file_path, a.thumbnail_path)
            if p
        }
    RecoveryService(service).startup()
    assert service.storage.quarantine_orphans(registered) == 2
    assert all(Path(a["file_path"]).exists() for _, a in clips)


def test_video_changed_after_acceptance_fails_freezing_and_releases_slot(
    client, video_file
):
    import asyncio

    _, episode, clips = timeline(client, video_file)
    data = preview(client, episode)
    service = client.app.state.task_service
    task = service.submit(command(data, body(data))).task
    path = Path(clips[0][1]["file_path"])
    path.chmod(0o644)
    path.write_bytes(b"corrupt after acceptance")
    asyncio.run(service.runner.run(task.id))
    result = service.get(task.id)
    assert result.status == "FAILED" and result.error_code == "ASSET_CHANGED"
    assert result.output_asset_id is None
    assert service.active().runtime_state.state == "IDLE"
    assert not client.get(f"/api/v1/episodes/{episode['id']}/exports").json()
