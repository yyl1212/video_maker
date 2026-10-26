"""从空库到有声成片的完整业务回归；只替换外部模型推理。"""

import hashlib
import json
import socket
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image


def test_empty_workspace_to_reference_driven_audio_film(client, monkeypatch):
    from workflow_fixtures import FixtureModels, install_models

    models = FixtureModels()
    install_models(client.app, models)

    # 业务闭环没有远端模型或云端回退；意外出站连接立即让测试失败。
    def no_network(*_args, **_kwargs):
        raise AssertionError("自动闭环不得连接外部推理服务")

    monkeypatch.setattr(socket.socket, "connect", no_network)

    def api(method, path, body=None):
        response = (
            client.request(method, "/api/v1" + path, json=body)
            if body is not None
            else client.request(method, "/api/v1" + path)
        )
        assert response.status_code < 300, response.text
        return response.json()

    def task(path, body):
        response = client.post(
            "/api/v1" + path, json={"idempotency_key": str(uuid4()), **body}
        )
        assert response.status_code == 202, response.text
        result = api("GET", f"/jobs/{response.json()['job_id']}")
        assert result["status"] == "DONE", result
        return result

    assert api("GET", "/projects") == []
    project = api(
        "POST",
        "/projects",
        {
            "name": "原创完整闭环",
            "style_anchor": "暖光纸质微缩布景",
            "characters": [
                {
                    "id": "robot",
                    "name": "小木",
                    "description": "橄榄绿木制机器人，橙围巾",
                }
            ],
        },
    )
    episode = api(
        "POST",
        f"/projects/{project['id']}/episodes",
        {"title": "问候", "script_text": "小木在门前挥手说：“你好。”"},
    )
    stream = BytesIO()
    Image.new("RGB", (128, 192), "#779966").save(stream, "PNG")
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("self-made.png", stream.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    reference = response.json()

    def versions():
        return {
            "expected_project_version": project["version"],
            "expected_episode_version": episode["version"],
        }

    parsed = task(f"/episodes/{episode['id']}/parse-jobs", versions())
    assert not api("GET", f"/episodes/{episode['id']}/shots")["shots"]
    review = api("GET", f"/jobs/{parsed['id']}/draft?project_id={project['id']}")
    episode = api(
        "POST",
        f"/jobs/{parsed['id']}/apply",
        {
            "project_id": project["id"],
            "expected_version": review["expected_version"],
            "edited_value": review["value"],
        },
    )
    shot = api("GET", f"/episodes/{episode['id']}/shots")["shots"][0]
    assert shot["status"] == "DRAFT" and shot["dialogue"] == "你好。"
    prompts = task(
        f"/shots/{shot['id']}/prompt-jobs",
        {**versions(), "expected_shot_version": shot["version"]},
    )
    review = api("GET", f"/jobs/{prompts['id']}/draft?project_id={project['id']}")
    shot = api(
        "POST",
        f"/jobs/{prompts['id']}/apply",
        {
            "project_id": project["id"],
            "expected_version": review["expected_version"],
            "edited_value": review["value"],
        },
    )
    assert (
        shot["dialogue"] == "你好。"
        and shot["compiled_image_prompt"].count("暖光纸质微缩布景") == 1
    )
    shot = api(
        "PUT",
        f"/shots/{shot['id']}/references",
        {
            "expected_version": shot["version"],
            "references": [{"asset_id": reference["id"], "role": "character"}],
        },
    )
    image = task(
        f"/shots/{shot['id']}/image-jobs",
        {
            **versions(),
            "expected_shot_version": shot["version"],
            "expected_image_input_hash": shot["image_input_hash"],
        },
    )
    assert (
        api("GET", f"/shots/{shot['id']}")["selection"]["first_frame_asset_id"] is None
    )
    frozen = models.image.requests[0].references[0]
    assert (
        "task-inputs" in str(frozen)
        and hashlib.sha256(frozen.read_bytes()).hexdigest() == reference["sha256"]
    )
    shot = api(
        "PUT",
        f"/shots/{shot['id']}/selection",
        {
            "shot_version": shot["version"],
            "selection_version": shot["selection"]["version"],
            "first_frame_asset_id": image["output_asset_id"],
        },
    )
    video = task(
        f"/shots/{shot['id']}/video-jobs",
        {
            **versions(),
            "expected_shot_version": shot["version"],
            "expected_selection_version": shot["selection"]["version"],
            "expected_video_input_hash": shot["video_input_hash"],
        },
    )
    assert (
        api("GET", f"/shots/{shot['id']}")["selection"]["selected_video_asset_id"]
        is None
    )
    frame_request = models.video.requests[0]
    assert "task-inputs" in str(frame_request.first_frame_path)
    assert "Spoken dialogue (verbatim): 你好。" in frame_request.prompt
    shot = api(
        "PUT",
        f"/shots/{shot['id']}/selection",
        {
            "shot_version": shot["version"],
            "selection_version": shot["selection"]["version"],
            "selected_video_asset_id": video["output_asset_id"],
        },
    )
    preview = api("GET", f"/episodes/{episode['id']}/export-preview")
    film = task(
        f"/episodes/{episode['id']}/export-jobs",
        {
            **versions(),
            "output_profile": "square",
            "clips": [
                {
                    "shot_id": shot["id"],
                    "review_token": preview["clips"][0]["review_token"],
                }
            ],
        },
    )
    exported = api("GET", f"/episodes/{episode['id']}/exports")[0]
    assert exported["id"] == film["output_asset_id"] and exported["has_audio"]
    assert exported["audio_sample_rate"] == 48000 and exported["audio_channels"] == 2
    content = client.get(
        f"/api/v1/assets/{exported['id']}/content?project_id={project['id']}&download=1"
    )
    assert hashlib.sha256(content.content).hexdigest() == exported["sha256"]
    manifest = api(
        "GET", f"/assets/{exported['id']}/manifest?project_id={project['id']}"
    )
    assert manifest["clips"][0]["asset_id"] == video["output_asset_id"]
    assert manifest["output"]["sha256"] == exported["sha256"]
    assert abs(exported["duration_seconds"] - 22 / 24) < 0.05
    assert len(models.image.requests) == len(models.video.requests) == 1
    assert all(
        Path(a["file_path"]).is_file()
        for a in api("GET", f"/projects/{project['id']}/assets")
    )
    assert client.app.state.task_service.active().runtime_state.state == "IDLE"
    print(json.dumps({"film_asset": exported["id"], "sha256": exported["sha256"]}))
