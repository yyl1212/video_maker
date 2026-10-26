from pathlib import Path

from test_assets_http import upload_image
from test_storyboard_jobs import done, install, prepare, review

from app.models import Shot, ShotSelection, Task


def apply(client, task, project, data, **extra):
    return client.post(
        f"/api/v1/jobs/{task['id']}/apply",
        json={
            "project_id": project["id"],
            "expected_version": data["expected_version"],
            "edited_value": data["value"],
            **extra,
        },
    )


def test_parse_replacement_needs_confirmation_and_preserves_old_media_and_history(
    client,
):
    project, episode, shot = prepare(client)
    install(client)
    asset = upload_image(client, project["id"]).json()
    client.put(
        f"/api/v1/shots/{shot['id']}/selection",
        json={
            "shot_version": shot["version"],
            "selection_version": 1,
            "first_frame_asset_id": asset["id"],
            "confirm_uploaded": True,
            "expected_image_input_hash": shot["image_input_hash"],
        },
    )
    task = done(client, project, episode)
    data = review(client, task, project)
    assert apply(client, task, project, data).status_code == 409
    data["value"]["shots"][0]["raw_description"] = "人工修订：门边的小林"
    response = apply(client, task, project, data, replace_confirm=True)
    assert response.status_code == 200, response.text
    shots = client.get(f"/api/v1/episodes/{episode['id']}/shots").json()["shots"]
    assert (
        len(shots) == 1
        and shots[0]["id"] != shot["id"]
        and shots[0]["raw_description"] == "人工修订：门边的小林"
    )
    assert (
        shots[0]["status"] == "DRAFT"
        and shots[0]["image_prompt"] == ""
        and shots[0]["video_prompt"] == ""
    )
    with client.app.state.database.session_scope() as session:
        assert session.get(Shot, shot["id"]).deleted_at
        assert (
            session.get(ShotSelection, shot["id"]).first_frame_asset_id == asset["id"]
        )
        assert session.get(Task, task["id"]).applied_at
    assert Path(asset["file_path"]).exists()
    assert apply(client, task, project, data, replace_confirm=True).status_code == 409
    assert (
        review(client, task, project)["value"]["shots"][0]["raw_description"]
        == "人工修订：门边的小林"
    )


def test_old_parse_rejects_edited_shot_when_episode_version_unchanged(
    client,
):
    project, episode, shot = prepare(client)
    install(client)
    task = done(client, project, episode)
    data = review(client, task, project)
    client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "dialogue": "人工修改"},
    )
    assert (
        client.get(f"/api/v1/episodes/{episode['id']}").json()["version"]
        == episode["version"]
    )
    assert apply(client, task, project, data, replace_confirm=True).status_code == 409
    assert not review(client, task, project)["apply_allowed"]
    assert not client.get(f"/api/v1/shots/{shot['id']}").json()["deleted_at"]


def test_prompt_apply_changes_only_prompts_preserves_dialogue_and_injects_style_once(
    client,
):
    project, episode, shot = prepare(client)
    install(client)
    task = done(client, project, episode, shot)
    data = review(client, task, project)
    data["value"]["video_prompt"] = "A gentle continuous push through the doorway."
    response = apply(client, task, project, data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["dialogue"] == "早上好"
        and result["raw_description"] == shot["raw_description"]
    )
    assert result["video_prompt"] == "A gentle continuous push through the doorway."
    assert result["compiled_image_prompt"].count("自然光") == 1
    assert "Spoken dialogue (verbatim): 早上好" in result["compiled_video_prompt"]
    assert (
        result["selection"] == shot["selection"] and result["reference_asset_ids"] == []
    )
    assert apply(client, task, project, data).status_code == 409


def test_changed_prompt_or_project_style_and_archived_targets_reject_apply(client):
    project, episode, shot = prepare(client)
    install(client)
    task = done(client, project, episode, shot)
    data = review(client, task, project)
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": project["version"], "style_anchor": "新风格"},
    )
    assert apply(client, task, project, data).status_code == 409
    assert not review(client, task, project)["apply_allowed"]
    current = client.get(f"/api/v1/projects/{project['id']}").json()
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": current["version"], "archived": True},
    )
    assert apply(client, task, project, data).status_code == 409


def test_irrelevant_project_name_change_does_not_invalidate_prompt_result(client):
    project, episode, shot = prepare(client)
    install(client)
    task = done(client, project, episode, shot)
    data = review(client, task, project)
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": project["version"], "name": "新项目名称"},
    )
    assert review(client, task, project)["apply_allowed"]
    assert apply(client, task, project, data).status_code == 200


def test_cross_project_invalid_role_and_wrong_draft_type_do_not_mutate_shots(client):
    project, episode, shot = prepare(client)
    install(client)
    task = done(client, project, episode)
    data = review(client, task, project)
    assert (
        client.get(f"/api/v1/jobs/{task['id']}/draft?project_id=999").status_code == 404
    )
    assert (
        apply(client, task, {"id": 999}, data, replace_confirm=True).status_code == 404
    )
    data["value"]["shots"][0]["character_ids"] = ["invented"]
    assert apply(client, task, project, data, replace_confirm=True).status_code == 422
    data["value"] = {
        "image_prompt": "Door",
        "video_prompt": "Opening",
        "audio_prompt": "Creaks",
    }
    assert apply(client, task, project, data, replace_confirm=True).status_code == 422
    assert (
        client.get(f"/api/v1/shots/{shot['id']}").json()["version"] == shot["version"]
    )
