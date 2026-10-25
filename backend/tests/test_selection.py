from pathlib import Path

from test_assets_http import make_project, upload_image
from test_shots import create_shot, workspace

from app.models import Asset, Task


def select_asset(client, shot, *, image=None, video=None, confirm=True, **versions):
    return client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": versions.get("shot_version", shot["version"]),
            "selection_version": versions.get(
                "selection_version", shot["selection"]["version"]
            ),
            "confirm_uploaded": confirm,
            **({"first_frame_asset_id": image} if image is not None else {}),
            **({"selected_video_asset_id": video} if video is not None else {}),
        },
    )


def test_selection_explicit_confirmation_scope_type_and_double_versions(client):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    asset = upload_image(client, project["id"]).json()
    other = upload_image(client, make_project(client, "别的")["id"]).json()
    assert select_asset(client, shot, image=other["id"]).status_code == 404
    assert select_asset(client, shot, video=asset["id"]).status_code == 422
    assert (
        select_asset(client, shot, image=asset["id"], confirm=False).status_code == 422
    )
    result = select_asset(client, shot, image=asset["id"])
    assert result.status_code == 200, result.text
    selected = result.json()
    assert selected["version"] == shot["version"]
    assert selected["selection"]["version"] == 2
    assert selected["video_ready"]
    assert not selected["selection"]["first_frame_stale"]
    assert select_asset(client, shot, image=asset["id"]).status_code == 409
    assert (
        select_asset(client, selected, image=asset["id"], shot_version=99).status_code
        == 409
    )
    path = Path(asset["file_path"])
    path.chmod(0o600)
    path.write_bytes(b"changed")
    assert select_asset(client, selected, image=asset["id"]).status_code == 409


def test_generated_asset_requires_same_shot_done_task_and_current_fingerprint(client):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    other = create_shot(client, episode["id"]).json()
    asset = upload_image(client, project["id"]).json()
    db = client.app.state.database
    with db.session_scope(write=True) as session:
        task = Task(
            project_id=project["id"],
            episode_id=episode["id"],
            shot_id=shot["id"],
            kind="IMAGE",
            status="DONE",
            request_hash="request",
            input_hash=shot["image_input_hash"],
            input_snapshot={},
            idempotency_key="generated",
            output_asset_id=asset["id"],
        )
        session.add(task)
        session.flush()
        record = session.get(Asset, asset["id"])
        record.source_task_id = task.id
        record.shot_id = shot["id"]
    assert select_asset(client, other, image=asset["id"]).status_code == 422
    selected = select_asset(client, shot, image=asset["id"]).json()
    updated = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": 1, "image_prompt": "新提示"},
    ).json()
    assert updated["selection"]["first_frame_stale"]
    assert select_asset(client, updated, image=asset["id"]).status_code == 409
    assert selected["selection"]["first_frame_asset_id"] == asset["id"]


def test_changing_first_frame_marks_selected_video_stale_without_overwrite(
    client, video_file
):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    first = upload_image(client, project["id"]).json()
    selected = select_asset(client, shot, image=first["id"]).json()
    with video_file.open("rb") as source:
        video = client.post(
            f"/api/v1/projects/{project['id']}/assets",
            files={"file": ("原创.mp4", source, "video/mp4")},
        ).json()
    selected = select_asset(client, selected, video=video["id"]).json()
    assert not selected["selection"]["video_stale"]
    # 使用不同像素内容，证明以内容哈希而非asset ID判定。
    from io import BytesIO

    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (64, 64), "blue").save(stream, "PNG")
    second = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("另一帧.png", stream.getvalue(), "image/png")},
    ).json()
    changed = select_asset(client, selected, image=second["id"]).json()
    assert changed["selection"]["selected_video_asset_id"] == video["id"]
    assert changed["selection"]["video_stale"]
    assert changed["selection"]["first_frame_asset_id"] == second["id"]


def test_archived_episode_and_deleted_shot_reject_selection(client):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    asset = upload_image(client, project["id"]).json()
    current = client.get(f"/api/v1/episodes/{episode['id']}").json()
    client.patch(
        f"/api/v1/episodes/{episode['id']}",
        json={"expected_version": current["version"], "archived": True},
    )
    assert select_asset(client, shot, image=asset["id"]).status_code == 409
