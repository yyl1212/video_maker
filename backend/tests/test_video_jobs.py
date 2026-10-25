from pathlib import Path
from uuid import uuid4

import pytest
from test_image_jobs import prepare as image_workspace

from app.errors import BackendError
from app.models import Asset, RuntimeState, Shot, ShotSelection


def workspace(client):
    project, episode, shot, refs = image_workspace(client)
    shot = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={
            "expected_version": shot["version"],
            "video_prompt": "The original robot waves slowly.",
            "audio_prompt": "Soft wooden joint clicks. No music.",
            "duration_seconds": 1,
            "video_settings": {"width": 32, "height": 32, "preset": "fast", "seed": 0},
        },
    ).json()
    shot = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": shot["version"],
            "selection_version": shot["selection"]["version"],
            "first_frame_asset_id": refs[0]["id"],
            "confirm_uploaded": True,
            "expected_image_input_hash": shot["image_input_hash"],
        },
    ).json()
    return project, episode, shot, refs


class FakeH3:
    def __init__(self):
        self.calls = []
        self.error = None
        self.edit = None

    async def generate(self, request, on_progress, *, on_dispatch, on_terminal, io):
        from app.media.video_profile import RawAVResult

        await on_dispatch()
        self.calls.append(request)
        if self.error:
            raise self.error
        await on_terminal()
        if self.edit:
            await io(self.edit)
        p = request.profile
        raw, pcm = request.directory / "frames.rgb", request.directory / "audio.pcm"
        await io(
            raw.write_bytes, bytes([35, 90, 50]) * p.width * p.height * p.num_frames
        )
        rate = 8000
        data = b"\x01\x00" * round(p.actual_duration * rate)
        await io(pcm.write_bytes, data)
        return RawAVResult(raw, pcm, p, rate, 1, len(data) / (rate * 2))


def install(client):
    from app.services.generation import VideoGenerationHandler

    service = client.app.state.task_service
    fake = FakeH3()
    service.handlers["VIDEO"] = VideoGenerationHandler(
        service.database, service.storage, fake
    )
    return service, fake


def body(project, episode, shot):
    return {
        "idempotency_key": str(uuid4()),
        "expected_project_version": project["version"],
        "expected_episode_version": episode["version"],
        "expected_shot_version": shot["version"],
        "expected_selection_version": shot["selection"]["version"],
        "expected_video_input_hash": shot["video_input_hash"],
    }


def submit(client, shot, data):
    return client.post(f"/api/v1/shots/{shot['id']}/video-jobs", json=data)


def test_frozen_first_frame_generates_native_audio_history_and_explicit_adoption(
    client,
):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    data = body(project, episode, shot)
    response = submit(client, shot, data)
    assert response.status_code == 202, response.text
    task = service.get(response.json()["job_id"])
    assert task.status == "DONE", task
    request = fake.calls[0]
    assert "task-inputs" in str(request.first_frame_path)
    assert request.first_frame_sha256 == refs[0]["sha256"]
    assert request.settings.seed == 0 and request.profile.num_frames == 22
    with service.database.session_scope() as session:
        asset = session.get(Asset, task.output_asset_id)
        assert (
            asset.has_audio
            and asset.audio_sample_rate == 48000
            and asset.audio_channels == 2
        )
        assert (
            asset.tags == ["video_clip"]
            and asset.metadata_json["first_frame"]["asset_id"] == refs[0]["id"]
        )
        assert session.get(ShotSelection, shot["id"]).selected_video_asset_id is None
        assert session.get(RuntimeState, 1).state == "IDLE"
    again = submit(client, shot, data)
    assert again.json()["job_id"] == task.id and len(fake.calls) == 1
    adopted = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": shot["version"],
            "selection_version": shot["selection"]["version"],
            "selected_video_asset_id": task.output_asset_id,
        },
    )
    assert adopted.status_code == 200, adopted.text


def test_regeneration_keeps_current_video_and_first_frame(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    first = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    shot = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": shot["version"],
            "selection_version": shot["selection"]["version"],
            "selected_video_asset_id": first.output_asset_id,
        },
    ).json()
    second = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    assert second.status == "DONE" and first.output_asset_id != second.output_asset_id
    latest = client.get(f"/api/v1/shots/{shot['id']}").json()
    assert latest["selection"]["selected_video_asset_id"] == first.output_asset_id
    assert latest["selection"]["first_frame_asset_id"] == refs[0]["id"]


def test_replaced_selection_and_changed_defaults_are_rejected(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    data = body(project, episode, shot)
    with service.database.session_scope(write=True) as session:
        session.get(ShotSelection, shot["id"]).version += 1
    assert submit(client, shot, data).status_code == 409
    assert not fake.calls


@pytest.mark.parametrize("problem", ["no_frame", "stale", "missing_file", "no_prompt"])
def test_invalid_first_frame_or_video_configuration_never_dispatches(client, problem):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    with service.database.session_scope(write=True) as session:
        if problem == "no_frame":
            session.get(ShotSelection, shot["id"]).first_frame_asset_id = None
        elif problem == "stale":
            session.get(Shot, shot["id"]).image_prompt = "changed picture"
        elif problem == "no_prompt":
            session.get(Shot, shot["id"]).video_prompt = ""
        else:
            Path(refs[0]["file_path"]).unlink()
    response = submit(client, shot, body(project, episode, shot))
    assert response.status_code in (409, 422)
    assert not fake.calls and service.active().runtime_state.state == "IDLE"


def test_edit_during_video_generation_keeps_stale_history(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)

    def edit():
        with service.database.session_scope(write=True) as session:
            record = session.get(Shot, shot["id"])
            record.video_prompt = "new motion"
            record.version += 1

    fake.edit = edit
    task = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    assert task.status == "DONE"
    latest = client.get(f"/api/v1/shots/{shot['id']}").json()
    adopt = client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": latest["version"],
            "selection_version": latest["selection"]["version"],
            "selected_video_asset_id": task.output_asset_id,
        },
    )
    assert adopt.status_code == 409


def test_interrupted_h3_stream_requires_mlx_reset(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    fake.error = BackendError("H3_STREAM_INTERRUPTED", "流中断", True)
    task = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    assert task.status == "FAILED" and task.output_asset_id is None
    assert service.active().runtime_state.state == "NEEDS_RESET"
    assert service.active().runtime_state.affected_backend == "mlx"


def test_video_input_path_injection_and_image_video_slot_are_protected(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    data = body(project, episode, shot)
    assert (
        submit(client, shot, {**data, "first_frame_image": "arbitrary"}).status_code
        == 422
    )
    from unittest.mock import patch

    async def hold(*args):
        pass

    with patch.object(service.runner, "run", new=hold):
        assert submit(client, shot, data).status_code == 202
    image = client.post(
        f"/api/v1/shots/{shot['id']}/image-jobs",
        json={
            "idempotency_key": str(uuid4()),
            "expected_project_version": project["version"],
            "expected_episode_version": episode["version"],
            "expected_shot_version": shot["version"],
            "expected_image_input_hash": shot["image_input_hash"],
        },
    )
    assert image.status_code == 429 and not fake.calls


def test_temporary_rgb_pcm_are_removed_after_publish_and_encoding_failure(client):
    project, episode, shot, refs = workspace(client)
    service, fake = install(client)
    job = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    directory = service.storage.root / "jobs" / str(job.id)
    assert job.status == "DONE"
    assert (
        not (directory / "frames.rgb").exists()
        and not (directory / "audio.pcm").exists()
    )
    assert Path(refs[0]["file_path"]).is_file()
    original = fake.generate

    async def empty_audio(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.pcm_path.write_bytes(b"")
        return result

    fake.generate = empty_audio
    failed = service.get(
        submit(client, shot, body(project, episode, shot)).json()["job_id"]
    )
    assert failed.status == "FAILED" and failed.output_asset_id is None
    assert service.active().runtime_state.state == "IDLE"
    assert not (service.storage.root / "jobs" / str(failed.id) / "frames.rgb").exists()
