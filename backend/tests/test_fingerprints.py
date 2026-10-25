from copy import deepcopy

from test_assets_http import upload_image
from test_selection import select_asset
from test_shots import create_shot, workspace

from app.prompts.compiler import compile_image, compile_video


def test_pure_compiler_has_stable_hash_and_only_actual_inputs():
    snapshot = {
        "style_anchor": "柔和自然光",
        "characters": [{"id": "a", "name": "小林", "description": "短发"}],
        "image_prompt": "小林推门",
        "references": [{"sha256": "abc", "role": "character"}],
        "image_settings": {"model": "local", "width": 768},
    }
    original = deepcopy(snapshot)
    result = compile_image(snapshot)
    assert snapshot == original
    assert "柔和自然光" in result.prompt and "Picture 1" in result.prompt
    unrelated = {
        **snapshot,
        "title": "另一集",
        "order_index": 5,
        "unused_assets": [999],
    }
    assert compile_image(unrelated).input_hash == result.input_hash
    assert (
        compile_image({**snapshot, "style_anchor": "水墨"}).input_hash
        != result.input_hash
    )
    assert (
        compile_image(
            {**snapshot, "references": [{"sha256": "new", "role": "character"}]}
        ).input_hash
        != result.input_hash
    )
    video = {
        "first_frame_sha256": "frame-a",
        "video_prompt": "慢慢推门",
        "audio_prompt": "门响",
        "dialogue": "你好",
        "duration_seconds": 5,
        "video_settings": {"seed": 42},
    }
    assert (
        compile_video(video).input_hash
        != compile_video({**video, "first_frame_sha256": "frame-b"}).input_hash
    )
    assert (
        compile_video(video).input_hash
        == compile_video({**video, "style_anchor": "无关"}).input_hash
    )


def test_style_and_selected_character_stale_but_title_order_unused_asset_do_not(client):
    project, episode = workspace(client)
    project = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "expected_version": 1,
            "style_anchor": "自然光",
            "characters": [
                {"id": "a", "name": "小林", "description": "短发"},
                {"id": "b", "name": "小周", "description": "长发"},
            ],
        },
    ).json()
    shot = create_shot(client, episode["id"], character_ids=["a"]).json()
    asset = upload_image(client, project["id"]).json()
    selected = select_asset(client, shot, image=asset["id"]).json()
    upload_image(client, project["id"])
    ep = client.get(f"/api/v1/episodes/{episode['id']}").json()
    client.patch(
        f"/api/v1/episodes/{episode['id']}",
        json={"expected_version": ep["version"], "title": "改标题"},
    )
    project = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "expected_version": project["version"],
            "name": "改项目名",
            "characters": [
                {"id": "a", "name": "小林", "description": "短发"},
                {"id": "b", "name": "小周", "description": "换衣服"},
            ],
        },
    ).json()
    current = client.get(f"/api/v1/shots/{shot['id']}").json()
    assert current["image_input_hash"] == selected["image_input_hash"]
    assert not current["selection"]["first_frame_stale"]
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": project["version"], "style_anchor": "水墨"},
    )
    changed = client.get(f"/api/v1/shots/{shot['id']}").json()
    assert changed["selection"]["first_frame_stale"] and not changed["video_ready"]
