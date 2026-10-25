from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import Asset, Task


def image_bytes(fmt="PNG"):
    stream = BytesIO()
    Image.new("RGB", (96, 64), "#99aa88").save(stream, format=fmt)
    return stream.getvalue()


def make_project(client, name="素材工程"):
    return client.post("/api/v1/projects", json={"name": name}).json()


def upload_image(client, project_id, **kwargs):
    return client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("../../原创.png", image_bytes(), "image/png")},
        **kwargs,
    )


def test_upload_limits_and_real_media_type(client, monkeypatch):
    project = make_project(client)
    response = upload_image(client, project["id"])
    assert response.status_code == 201
    asset = response.json()
    assert asset["type"] == "image" and asset["mime_type"] == "image/png"
    assert asset["width"] == 96 and ".." not in asset["file_path"]
    jpeg = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("test.jpg", image_bytes("JPEG"), "image/jpeg")},
    )
    assert jpeg.json()["mime_type"] == "image/png"
    fake = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("fake.png", b"not a picture", "image/png")},
    )
    assert fake.status_code == 422
    from app.services import assets

    monkeypatch.setattr(assets, "IMAGE_LIMIT", 10)
    assert upload_image(client, project["id"]).status_code == 413
    assert len(client.get(f"/api/v1/projects/{project['id']}/assets").json()) == 2


def test_cross_project_content_and_range_access(client, video_file):
    project, other = make_project(client), make_project(client, "其他")
    with video_file.open("rb") as stream:
        response = client.post(
            f"/api/v1/projects/{project['id']}/assets",
            files={"file": ("原创.mp4", stream, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["has_audio"] and asset["audio_sample_rate"] == 48000
    url = f"/api/v1/assets/{asset['id']}/content"
    assert client.get(url, params={"project_id": other["id"]}).status_code == 404
    result = client.get(
        url, params={"project_id": project["id"]}, headers={"Range": "bytes=0-31"}
    )
    assert result.status_code == 206 and len(result.content) == 32
    assert result.headers["content-range"].startswith("bytes 0-31/")
    assert (
        client.get(
            url,
            params={"project_id": project["id"]},
            headers={"Range": "bytes=999999999-"},
        ).status_code
        == 416
    )
    assert (
        client.get(
            f"/api/v1/assets/{asset['id']}/thumbnail",
            params={"project_id": project["id"]},
        ).status_code
        == 200
    )


def test_upload_database_failure_quarantines_file(client, monkeypatch):
    project = make_project(client)
    from app.services.assets import AssetService

    def broken(*args, **kwargs):
        from app.errors import AppError

        raise AppError(503, "DATABASE_UNAVAILABLE", "写入失败")

    monkeypatch.setattr(AssetService, "register", broken)
    assert upload_image(client, project["id"]).status_code == 503
    with client.app.state.database.session_scope() as session:
        assert not session.scalars(select(Asset)).all()
    storage = client.app.state.storage
    assert not list(storage.media.rglob("*.png"))
    assert list(storage.quarantine.iterdir())


def test_asset_patch_delete_archive_and_hash_validation(client):
    project = make_project(client)
    asset = upload_image(client, project["id"]).json()
    url = f"/api/v1/assets/{asset['id']}"
    updated = client.patch(
        url, json={"expected_version": 1, "name": "角色正面", "tags": ["reference"]}
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.patch(url, json={"expected_version": 1, "name": "旧"}).status_code == 409
    )
    from pathlib import Path

    path = Path(asset["file_path"])
    path.chmod(0o600)
    path.write_bytes(b"modified")
    assert (
        client.get(url + "/content", params={"project_id": project["id"]}).status_code
        == 409
    )
    assert (
        client.request("DELETE", url, json={"expected_version": 2}).status_code == 200
    )
    assert (
        client.get(url + "/content", params={"project_id": project["id"]}).status_code
        == 404
    )
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": 1, "archived": True},
    )
    assert upload_image(client, project["id"]).status_code == 409


def test_history_reference_prevents_deletion(client):
    project = make_project(client)
    asset = upload_image(client, project["id"]).json()
    episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes", json={"title": "一"}
    ).json()
    with client.app.state.database.session_scope(write=True) as session:
        session.add(
            Task(
                project_id=project["id"],
                episode_id=episode["id"],
                kind="EXPORT",
                status="FAILED",
                input_snapshot={"clips": [{"asset_id": asset["id"]}]},
                input_hash="a",
                request_hash="b",
                idempotency_key="history",
            )
        )
    assert (
        client.request(
            "DELETE", f"/api/v1/assets/{asset['id']}", json={"expected_version": 1}
        ).status_code
        == 409
    )


def test_upload_body_limit_with_and_without_content_length(client, monkeypatch):
    from app import upload_limit

    monkeypatch.setattr(upload_limit, "MAX_MULTIPART_BYTES", 128)
    project = make_project(client)
    assert upload_image(client, project["id"]).status_code == 413
    chunks = iter(
        [
            b'--boundary\r\nContent-Disposition: form-data; name="file"; '
            b'filename="x.png"\r\n\r\n',
            b"x" * 256,
            b"\r\n--boundary--\r\n",
        ]
    )
    result = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        content=chunks,
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )
    assert result.status_code == 413
    assert result.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


@pytest.mark.parametrize("trim", [30, 40, 60])
def test_truncated_image_is_invalid_input_and_not_storage_failure(client, trim):
    project = make_project(client)
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("broken.png", image_bytes()[:-trim], "image/png")},
    )
    assert response.status_code == 422


def test_tail_truncated_mp4_with_valid_metadata_is_rejected(client, tmp_path):
    import subprocess

    path = tmp_path / "faststart-original.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x96:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    truncated = path.read_bytes()[: path.stat().st_size // 2]
    project = make_project(client)
    result = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("tail-broken.mp4", truncated, "video/mp4")},
    )
    assert result.status_code == 422
    assert client.get(f"/api/v1/projects/{project['id']}/assets").json() == []
    assert not list(client.app.state.storage.media.rglob("*.mp4"))
