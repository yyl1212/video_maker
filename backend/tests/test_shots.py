from test_assets_http import make_project, upload_image


def workspace(client):
    project = make_project(client)
    episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes", json={"title": "原创第一集"}
    ).json()
    return project, episode


def create_shot(client, episode_id, **values):
    return client.post(
        f"/api/v1/episodes/{episode_id}/shots",
        json={
            "raw_description": "小林在清晨推开木门",
            "image_prompt": "清晨的木门与人物",
            "video_prompt": "人物缓慢推门",
            **values,
        },
    )


def test_shot_crud_ready_without_references_and_archive(client):
    project, episode = workspace(client)
    response = create_shot(client, episode["id"])
    assert response.status_code == 201, response.text
    shot = response.json()
    assert shot["status"] == "READY" and shot["reference_asset_ids"] == []
    assert not shot["video_ready"] and shot["selection"]["version"] == 1
    bad = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": 1, "duration_seconds": 9},
    )
    assert bad.status_code == 422
    saved = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": 1, "raw_description": "修改后的描述"},
    )
    assert saved.json()["version"] == 2
    assert (
        client.patch(
            f"/api/v1/shots/{shot['id']}",
            json={"expected_version": 1, "dialogue": "旧"},
        ).status_code
        == 409
    )
    client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expected_version": 1, "archived": True},
    )
    assert create_shot(client, episode["id"]).status_code == 409
    assert (
        client.patch(
            f"/api/v1/shots/{shot['id']}",
            json={"expected_version": 2, "dialogue": "新"},
        ).status_code
        == 409
    )


def test_order_replacement_is_atomic_and_24_limit(client):
    _, episode = workspace(client)
    shots = [create_shot(client, episode["id"]).json() for _ in range(24)]
    assert all("id" in s for s in shots)
    assert create_shot(client, episode["id"]).status_code == 409
    listing = client.get(f"/api/v1/episodes/{episode['id']}/shots").json()
    url = f"/api/v1/episodes/{episode['id']}/shots/reorder"
    reversed_ids = [s["id"] for s in reversed(shots)]
    wrong = client.post(
        url,
        json={
            "expected_version": listing["episode_version"],
            "shot_ids": reversed_ids[:-1],
        },
    )
    assert wrong.status_code == 422
    result = client.post(
        url,
        json={"expected_version": listing["episode_version"], "shot_ids": reversed_ids},
    )
    assert result.status_code == 200, result.text
    assert [s["id"] for s in result.json()] == reversed_ids
    assert [s["order_index"] for s in result.json()] == list(range(1, 25))
    assert (
        client.post(
            url,
            json={
                "expected_version": listing["episode_version"],
                "shot_ids": reversed_ids,
            },
        ).status_code
        == 409
    )
    deleted = result.json()[2]
    assert (
        client.request(
            "DELETE",
            f"/api/v1/shots/{deleted['id']}",
            json={"expected_version": deleted["version"]},
        ).status_code
        == 200
    )
    assert create_shot(client, episode["id"]).status_code == 201
    current = client.get(f"/api/v1/episodes/{episode['id']}/shots").json()["shots"]
    assert [s["order_index"] for s in current] == list(range(1, 25))
    assert (
        client.patch(
            f"/api/v1/shots/{deleted['id']}",
            json={"expected_version": deleted["version"], "dialogue": "x"},
        ).status_code
        == 404
    )


def test_ordered_references_scope_roles_duplicates_missing_and_max(client):
    project, episode = workspace(client)
    shot = create_shot(client, episode["id"]).json()
    a, b = [upload_image(client, project["id"]).json() for _ in range(2)]
    other = upload_image(client, make_project(client, "别的项目")["id"]).json()
    url = f"/api/v1/shots/{shot['id']}/references"
    for refs, status in [
        ([{"asset_id": a["id"]}, {"asset_id": a["id"]}], 422),
        ([{"asset_id": other["id"]}], 404),
        ([{"asset_id": 9999}], 404),
        ([{"asset_id": a["id"], "role": "unknown"}], 422),
        ([{"asset_id": n} for n in range(1, 10)], 422),
    ]:
        result = client.put(url, json={"expected_version": 1, "references": refs})
        assert result.status_code == status, result.text
    result = client.put(
        url,
        json={
            "expected_version": 1,
            "references": [
                {"asset_id": b["id"], "role": "scene"},
                {"asset_id": a["id"], "role": "character"},
            ],
        },
    )
    assert result.status_code == 200, result.text
    current = result.json()
    assert current["reference_asset_ids"] == [b["id"], a["id"]]
    assert [r["position"] for r in current["references"]] == [0, 1]
    assert (
        client.request(
            "DELETE", f"/api/v1/assets/{a['id']}", json={"expected_version": 1}
        ).status_code
        == 409
    )
    assert (
        client.put(url, json={"expected_version": 1, "references": []}).status_code
        == 409
    )
    assert (
        client.put(url, json={"expected_version": 2, "references": []}).status_code
        == 200
    )


def test_unknown_characters_and_settings_are_rejected(client):
    _, episode = workspace(client)
    for fields in (
        {"character_ids": ["ghost"]},
        {"image_settings": {"host": "evil"}},
        {"video_settings": {"width": 383}},
        {"image_settings": {"seed": -1}},
        {"video_settings": {"width": 672, "height": 672}},
    ):
        assert create_shot(client, episode["id"], **fields).status_code == 422
