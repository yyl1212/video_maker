"""通过真实 HTTP 边界验证归属、持久化、乐观锁和本机写保护。"""

import pytest
from fastapi.testclient import TestClient


def project(client, name="第一部短片"):
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_project_episode_scope_and_optimistic_version(client):
    first = project(client)
    second = project(client, "另一个项目")
    saved = client.patch(
        f"/api/v1/projects/{first['id']}",
        json={
            "expected_version": 1,
            "style_anchor": "自然光，电影感",
            "characters": [{"id": "hero", "name": "小林", "description": "年轻旅人"}],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 2
    assert saved.json()["updated_at"].endswith("Z")
    conflict = client.patch(
        f"/api/v1/projects/{first['id']}",
        json={
            "expected_version": 1,
            "name": "旧页面覆盖",
        },
    )
    assert conflict.status_code == 409
    assert client.get(f"/api/v1/projects/{first['id']}").json()["name"] == "第一部短片"
    response = client.post(
        f"/api/v1/projects/{first['id']}/episodes",
        json={
            "title": "第一集",
            "script_text": "清晨，小林走进街道。",
        },
    )
    assert response.status_code == 201
    episode = response.json()
    assert episode["project_id"] == first["id"]
    assert client.get(f"/api/v1/projects/{second['id']}/episodes").json() == []
    assert (
        client.get(f"/api/v1/episodes/{episode['id']}").json()["script_text"]
        == "清晨，小林走进街道。"
    )
    assert (
        client.patch(
            f"/api/v1/episodes/{episode['id']}",
            json={
                "expected_version": 1,
                "project_id": second["id"],
            },
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/episodes/{episode['id']}",
            json={
                "expected_version": 1,
                "script_text": "修改后的原创剧本。",
            },
        ).json()["version"]
        == 2
    )
    assert (
        client.patch(
            f"/api/v1/episodes/{episode['id']}",
            json={
                "expected_version": 1,
                "title": "旧标题",
            },
        ).status_code
        == 409
    )


def test_archive_is_read_only_and_can_restore(client):
    item = project(client)
    episode = client.post(
        f"/api/v1/projects/{item['id']}/episodes", json={"title": "开场"}
    ).json()
    endpoint = f"/api/v1/projects/{item['id']}"
    assert (
        client.patch(
            endpoint, json={"expected_version": 1, "archived": True}
        ).status_code
        == 200
    )
    assert client.get(endpoint).status_code == 200
    assert (
        client.post(f"{endpoint}/episodes", json={"title": "不应创建"}).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/episodes/{episode['id']}",
            json={
                "expected_version": 1,
                "script_text": "不能写入归档项目",
            },
        ).status_code
        == 409
    )
    assert (
        client.patch(
            endpoint, json={"expected_version": 2, "name": "不能编辑"}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            endpoint, json={"expected_version": 2, "archived": False}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            endpoint, json={"expected_version": 3, "name": "恢复后的片名"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"name": "片" * 121},
        {"name": "测试", "style_anchor": "字" * 4001},
        {
            "name": "测试",
            "characters": [
                {"id": "same", "name": "甲", "description": ""},
                {"id": "same", "name": "乙", "description": ""},
            ],
        },
    ],
)
def test_invalid_project_input_is_rejected(client, payload):
    assert client.post("/api/v1/projects", json=payload).status_code == 422
    assert client.get("/api/v1/projects").json() == []


def test_script_limit_missing_target_and_strict_version(client):
    item = project(client)
    endpoint = f"/api/v1/projects/{item['id']}/episodes"
    assert (
        client.post(
            endpoint, json={"title": "完整", "script_text": "字" * 12000}
        ).status_code
        == 201
    )
    assert (
        client.post(
            endpoint, json={"title": "过长", "script_text": "字" * 12001}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/episodes/99999").status_code == 404
    assert (
        client.post(
            "/api/v1/projects/99999/episodes", json={"title": "不存在"}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/projects/{item['id']}",
            json={
                "expected_version": True,
                "name": "错误版本",
            },
        ).status_code
        == 422
    )


def test_write_requires_session_and_local_origin(client):
    assert (
        client.post(
            "/api/v1/projects",
            json={"name": "无令牌"},
            headers={"X-Local-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/projects",
            json={"name": "跨站"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/projects", headers={"Host": "evil.example"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/projects",
            json={"name": "同源"},
            headers={"Origin": "http://127.0.0.1:3000"},
        ).status_code
        == 201
    )


def test_settings_are_local_versioned_and_do_not_generate(client):
    original = client.get("/api/v1/settings").json()
    assert original["version"] == 1
    changed = client.put(
        "/api/v1/settings",
        json={
            "expected_version": 1,
            "text": {**original["text"], "base_url": "http://localhost:1235/v1"},
            "image": original["image"],
            "video": original["video"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    invalid = client.put(
        "/api/v1/settings",
        json={
            "expected_version": 2,
            "text": {**original["text"], "base_url": "https://remote.example/v1"},
            "image": original["image"],
            "video": original["video"],
        },
    )
    assert invalid.status_code == 422
    assert client.get("/api/v1/bootstrap").json()["capabilities"]["generation"] is True
    assert client.get("/api/v1/jobs/active").json()["task"] is None


def test_database_survives_app_restart(tmp_path):
    from app.config import AppSettings
    from app.main import create_app

    settings = AppSettings(data_dir=tmp_path / "persistent")
    for name in ("首次创建", None):
        with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as http:
            if name:
                http.headers["X-Local-Token"] = http.get("/api/v1/bootstrap").json()[
                    "token"
                ]
                project(http, name)
            assert http.get("/api/v1/projects").json()[0]["name"] == "首次创建"


def test_no_cloud_cdn_documentation_pages(client):
    # 默认Swagger/ReDoc页面从外部CDN加载脚本，本机产品不应暴露这些入口。
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_openapi_describes_the_actual_validation_error(client):
    schema = client.get("/openapi.json").json()
    invalid = client.post("/api/v1/projects", json={"name": ""})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    documented = schema["paths"]["/api/v1/projects"]["post"]["responses"]["422"]
    assert documented["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorRead"
    )


@pytest.mark.parametrize(
    "forwarded",
    [
        "external.example:3000",
        "localhost:3000, external.example",
        "evil@localhost:3000",
    ],
)
def test_forwarded_external_host_cannot_read_local_data(client, forwarded):
    for endpoint in ("/api/v1/bootstrap", "/api/v1/projects", "/api/v1/episodes/1"):
        response = client.get(
            endpoint,
            headers={
                "X-Forwarded-Host": forwarded,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 403
        assert set(response.json()) == {"error"}
