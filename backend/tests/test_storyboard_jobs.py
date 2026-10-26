import asyncio
from uuid import uuid4

from test_llm_client import PARSED, PROMPTS
from test_shots import create_shot, workspace

from app.errors import BackendError
from app.models import ModelSettings


def prepare(client):
    project, episode = workspace(client)
    project = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "expected_version": project["version"],
            "style_anchor": "自然光",
            "characters": [{"id": "lin", "name": "小林", "description": "短发"}],
        },
    ).json()
    episode = client.patch(
        f"/api/v1/episodes/{episode['id']}",
        json={
            "expected_version": episode["version"],
            "script_text": "小林推门，说：“早上好”。",
        },
    ).json()
    shot = create_shot(
        client, episode["id"], character_ids=["lin"], dialogue="早上好"
    ).json()
    episode = client.get(f"/api/v1/episodes/{episode['id']}").json()
    with client.app.state.database.session_scope(write=True) as session:
        session.get(ModelSettings, 1).text_settings = {
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "local-text",
        }
    return project, episode, shot


class FakeLLM:
    def __init__(self):
        self.requests = []
        self.error = None

    async def run(self, request, on_dispatch, on_terminal, **_):
        self.requests.append(request)
        await on_dispatch()
        if self.error:
            raise self.error
        await on_terminal()

    async def parse_script(self, request, **kwargs):
        from app.schemas.storyboards import StoryboardDraft

        await self.run(request, **kwargs)
        return StoryboardDraft.model_validate(PARSED)

    async def optimize_prompts(self, request, **kwargs):
        from app.schemas.storyboards import PromptDraft

        await self.run(request, **kwargs)
        return PromptDraft.model_validate(PROMPTS)


def install(client):
    fake = FakeLLM()
    for kind in ("PARSE_SCRIPT", "OPTIMIZE_PROMPTS"):
        client.app.state.task_service.handlers[kind].client = fake
    return fake


def submit_body(project, episode, shot=None):
    value = {
        "idempotency_key": str(uuid4()),
        "expected_project_version": project["version"],
        "expected_episode_version": episode["version"],
    }
    if shot:
        value["expected_shot_version"] = shot["version"]
    return value


def done(client, project, episode, shot=None):
    url = (
        f"/api/v1/shots/{shot['id']}/prompt-jobs"
        if shot
        else f"/api/v1/episodes/{episode['id']}/parse-jobs"
    )
    response = client.post(url, json=submit_body(project, episode, shot))
    assert response.status_code == 202, response.text
    task = client.get(response.json()["poll_url"]).json()
    assert task["status"] == "DONE", task
    return task


def review(client, task, project):
    response = client.get(f"/api/v1/jobs/{task['id']}/draft?project_id={project['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_parse_only_saves_draft_and_idempotent_replay_never_calls_llm_again(client):
    project, episode, shot = prepare(client)
    fake = install(client)
    request = submit_body(project, episode)
    url = f"/api/v1/episodes/{episode['id']}/parse-jobs"
    accepted = client.post(url, json=request)
    assert accepted.status_code == 202, accepted.text
    task = client.get(accepted.json()["poll_url"]).json()
    assert task["status"] == "DONE", task
    assert (
        len(fake.requests) == 1
        and fake.requests[0].script_text == "小林推门，说：“早上好”。"
    )
    assert (
        client.get(f"/api/v1/episodes/{episode['id']}/shots").json()["shots"][0]["id"]
        == shot["id"]
    )
    assert (
        client.post(url, json=request).json()["job_id"] == task["id"]
        and len(fake.requests) == 1
    )
    data = review(client, task, project)
    assert data["value"] == PARSED and data["apply_allowed"]


def test_prompt_draft_keeps_dialogue_and_formal_shot_until_apply(client):
    project, episode, shot = prepare(client)
    fake = install(client)
    task = done(client, project, episode, shot)
    assert (
        fake.requests[0].dialogue == "早上好"
        and fake.requests[0].characters[0]["id"] == "lin"
    )
    unchanged = client.get(f"/api/v1/shots/{shot['id']}").json()
    assert (
        unchanged["image_prompt"] == shot["image_prompt"]
        and unchanged["dialogue"] == "早上好"
    )
    assert review(client, task, project)["value"] == PROMPTS


def test_accepted_parse_uses_frozen_script_and_roles_after_source_edits(client):
    from app.schemas.tasks import TaskSubmitCommand

    project, episode, shot = prepare(client)
    fake = install(client)
    service = client.app.state.task_service
    request = submit_body(project, episode)
    command = TaskSubmitCommand(
        project_id=project["id"],
        episode_id=episode["id"],
        kind="PARSE_SCRIPT",
        **request,
    )
    task = service.submit(command).task
    client.patch(
        f"/api/v1/episodes/{episode['id']}",
        json={"expected_version": episode["version"], "script_text": "后来改写的故事"},
    )
    asyncio.run(service.runner.run(task.id))
    assert fake.requests[0].script_text == "小林推门，说：“早上好”。"
    result = review(client, {"id": task.id}, project)
    assert not result["apply_allowed"] and result["conflict_reason"]


def test_failed_model_retains_unknown_remote_state_without_replay(client):
    project, episode, _ = prepare(client)
    fake = install(client)
    fake.error = BackendError("LLM_TIMEOUT", "模型超时", True)
    response = client.post(
        f"/api/v1/episodes/{episode['id']}/parse-jobs",
        json=submit_body(project, episode),
    )
    assert response.status_code == 202
    task = client.get(response.json()["poll_url"]).json()
    assert task["status"] == "FAILED" and task["error_code"] == "LLM_TIMEOUT"
    assert client.app.state.task_service.active().runtime_state.state == "NEEDS_RESET"


def test_empty_script_unconfigured_model_and_bad_scope_refused_before_generation(
    client,
):
    project, episode, shot = prepare(client)
    install(client)
    with client.app.state.database.session_scope(write=True) as session:
        session.get(ModelSettings, 1).text_settings = {"model": ""}
    response = client.post(
        f"/api/v1/episodes/{episode['id']}/parse-jobs",
        json=submit_body(project, episode),
    )
    assert response.status_code == 422
    assert (
        client.post(
            f"/api/v1/shots/{shot['id']}/prompt-jobs",
            json=submit_body(project, episode),
        ).status_code
        == 422
    )


def test_prompt_retry_after_target_deletion_still_returns_original_job(client):
    project, episode, shot = prepare(client)
    fake = install(client)
    request = submit_body(project, episode, shot)
    url = f"/api/v1/shots/{shot['id']}/prompt-jobs"
    response = client.post(url, json=request)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert (
        client.request(
            "DELETE",
            f"/api/v1/shots/{shot['id']}",
            json={"expected_version": shot["version"]},
        ).status_code
        == 200
    )
    replay = client.post(url, json=request)
    assert replay.status_code == 202 and replay.json()["job_id"] == job_id, replay.text
    assert len(fake.requests) == 1
