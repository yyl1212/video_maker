import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from app.errors import BackendError
from app.models.base import utc_now
from app.schemas.clients import TextSettings

PARSED = {
    "shots": [
        {
            "order_index": 1,
            "raw_description": "小林推门",
            "dialogue": "早上好",
            "character_ids": ["lin"],
            "audio_prompt": "Wooden door creaks.",
            "duration_seconds": 3,
        }
    ]
}
PROMPTS = {
    "image_prompt": "A short-haired person stands beside a wooden door.",
    "video_prompt": "The person slowly opens the door.",
    "audio_prompt": "A quiet room with soft door creaks.",
}


def request(text="小林推开门，说：“早上好”。", model="local-text"):
    from app.clients.llm import ParseRequest

    return ParseRequest(
        script_text=text,
        characters=[{"id": "lin", "name": "小林", "description": "短发"}],
        settings=TextSettings(model=model),
        deadline=utc_now() + timedelta(seconds=2),
    )


def response(value=PARSED, field="content", finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {field: json.dumps(value, ensure_ascii=False)},
            }
        ]
    }


def client(payload, calls=None, status=200):
    from app.clients.llm import LLMClient

    calls = [] if calls is None else calls

    async def route(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "local-text"}, {"id": "text-embedding-ignore"}]},
            )
        return httpx.Response(
            status,
            content=payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    return LLMClient(transport=httpx.MockTransport(route))


def test_structured_request_keeps_original_script_and_known_roles_only():
    calls = []
    value = asyncio.run(client(response(), calls).parse_script(request()))
    assert value.shots[0].dialogue == "早上好"
    sent = json.loads(calls[0].content)
    assert (
        calls[0].url.host == "127.0.0.1" and calls[0].url.path == "/v1/chat/completions"
    )
    assert sent["stream"] is False and sent["response_format"]["type"] == "json_schema"
    assert "小林推开门" in sent["messages"][1]["content"]
    assert (
        sent["response_format"]["json_schema"]["schema"]["additionalProperties"]
        is False
    )


@pytest.mark.parametrize(
    "model,allowed",
    [
        ("qwen3.8-27b-mlx", True),
        ("qwen3.8-27b-mlx@8bit", True),
        ("qwen3.8-27b-mlx@4bit", False),
        ("other-qwen", False),
        ("qwen3.8-27b-mlx@unverified", False),
    ],
)
def test_reasoning_whole_json_only_for_verified_model(model, allowed):
    if allowed:
        assert asyncio.run(
            client(response(field="reasoning_content")).parse_script(
                request(model=model)
            )
        ).shots
    else:
        with pytest.raises(BackendError):
            asyncio.run(
                client(response(field="reasoning_content")).parse_script(
                    request(model=model)
                )
            )


def test_reasoning_explanations_or_markdown_are_never_extracted():
    data = response(field="reasoning_content")
    data["choices"][0]["message"]["reasoning_content"] = "分析如下：" + json.dumps(
        PARSED
    )
    with pytest.raises(BackendError):
        asyncio.run(client(data).parse_script(request(model="qwen3.8-27b-mlx")))


@pytest.mark.parametrize(
    "change",
    [
        {"shots": []},
        {"shots": PARSED["shots"] * 25},
        {"shots": [{**PARSED["shots"][0], "order_index": 2}]},
        {"shots": [{**PARSED["shots"][0], "character_ids": ["invented"]}]},
        {"shots": [{**PARSED["shots"][0], "reference_asset_ids": [1]}]},
        {"shots": [{**PARSED["shots"][0], "dialogue": "Good morning"}]},
        {"shots": [{**PARSED["shots"][0], "duration_seconds": True}]},
    ],
)
def test_invalid_counts_ids_dialogue_and_asset_invention_rejected(change):
    with pytest.raises(BackendError):
        asyncio.run(client(response(change)).parse_script(request()))


def test_24_shots_and_12000_chars_accepted_but_larger_script_never_dispatched():
    many = {
        "shots": [
            {**PARSED["shots"][0], "order_index": i, "dialogue": ""}
            for i in range(1, 25)
        ]
    }
    assert (
        len(
            asyncio.run(
                client(response(many)).parse_script(request("字" * 12000))
            ).shots
        )
        == 24
    )
    calls = []
    with pytest.raises(BackendError):
        asyncio.run(client(response(), calls).parse_script(request("字" * 12001)))
    assert not calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"choices":[],"choices":[]}',
        json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"shots":[],"shots":[]}'},
                    }
                ]
            }
        ).encode(),
        json.dumps(response()).encode()[:-2],
    ],
)
def test_duplicate_json_keys_and_broken_response_rejected(raw):
    with pytest.raises(BackendError):
        asyncio.run(client(raw).parse_script(request()))


def test_terminal_truncation_and_http_rejection_release_known_remote():
    async def run(payload, status=200):
        events = []

        async def dispatch():
            events.append("dispatch")

        async def terminal():
            events.append("terminal")

        with pytest.raises(BackendError) as error:
            await client(payload, status=status).parse_script(
                request(), on_dispatch=dispatch, on_terminal=terminal
            )
        return events, error.value

    events, error = asyncio.run(run(response(finish="length")))
    assert events == ["dispatch", "terminal"] and not error.execution_uncertain
    events, error = asyncio.run(run({}, 422))
    assert events == ["dispatch", "terminal"] and not error.execution_uncertain
    _, error = asyncio.run(run({}, 500))
    assert error.execution_uncertain


def test_timeout_and_interrupted_body_keep_uncertain_remote():
    from app.clients.llm import LLMClient

    async def timeout_route(req):
        await asyncio.sleep(5)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            LLMClient(transport=httpx.MockTransport(timeout_route)).parse_script(
                replace(request(), deadline=utc_now() + timedelta(milliseconds=25))
            )
        )
    assert error.value.execution_uncertain

    async def broken(req):
        raise httpx.ReadError("private server detail")

    with pytest.raises(BackendError) as error:
        asyncio.run(
            LLMClient(transport=httpx.MockTransport(broken)).parse_script(request())
        )
    assert error.value.execution_uncertain and "private" not in error.value.message


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.example/v1",
        "http://127.0.0.1.evil/v1",
        "http://user@localhost/v1",
        "http://localhost/v1?x=1",
    ],
)
def test_non_local_and_credential_urls_rejected(url):
    with pytest.raises(ValidationError):
        TextSettings(base_url=url)


def test_connection_check_only_reads_models_and_preserves_localhost_tls_identity():
    calls = []
    result = asyncio.run(
        client({}, calls).test_connection(
            TextSettings(base_url="https://localhost:1234", model="local-text")
        )
    )
    assert result.models == ["local-text"]
    assert (
        len(calls) == 1
        and calls[0].method == "GET"
        and calls[0].url.host == "127.0.0.1"
    )
    assert calls[0].extensions["sni_hostname"] == "localhost"


def test_prompts_use_english_scene_text_with_verbatim_dialogue_outside_model_output():
    from app.clients.llm import PromptRequest

    req = PromptRequest(
        description="小林推开门",
        dialogue="早上好",
        characters=[],
        existing_prompts={"image_prompt": "", "video_prompt": "", "audio_prompt": ""},
        settings=TextSettings(model="local-text"),
        deadline=utc_now() + timedelta(seconds=2),
    )
    value = asyncio.run(client(response(PROMPTS)).optimize_prompts(req))
    assert value.model_dump() == PROMPTS
    with pytest.raises(BackendError):
        asyncio.run(
            client(response({**PROMPTS, "dialogue": "Good morning"})).optimize_prompts(
                req
            )
        )
    with pytest.raises(BackendError):
        asyncio.run(
            client(response({**PROMPTS, "video_prompt": "人物推门"})).optimize_prompts(
                req
            )
        )
