"""纯函数：只对实际模型输入做指纹，不含名称、排序或未使用素材。"""

import hashlib
import json
from dataclasses import dataclass

COMPILER_VERSION = "1"


@dataclass(frozen=True)
class CompiledPrompt:
    prompt: str
    input_hash: str
    parameters: dict


def content_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def actual_video_frames(duration: float) -> int:
    return min((17 * k + 5 for k in range(1, 12)), key=lambda n: abs(n - duration * 24))


def compile_image(snapshot: dict) -> CompiledPrompt:
    characters = [
        {"name": c["name"], "description": c["description"]}
        for c in snapshot.get("characters", [])
    ]
    references = [
        {"sha256": ref["sha256"], "role": ref["role"]}
        for ref in snapshot.get("references", [])
    ]
    sections = [snapshot.get("image_prompt", "").strip()]
    if style := snapshot.get("style_anchor", "").strip():
        sections.append(f"全局风格：{style}")
    if characters:
        sections.append(
            "角色：" + "；".join(f"{c['name']}：{c['description']}" for c in characters)
        )
    if references:
        roles = {"character": "人物参考", "scene": "场景参考", "other": "其他参考"}
        sections.append(
            "参考图："
            + "；".join(
                f"Picture {i + 1}（{roles[ref['role']]}）"
                for i, ref in enumerate(references)
            )
        )
    prompt = "\n".join(sections)
    parameters = dict(snapshot.get("image_settings", {}))
    inputs = {
        "compiler_version": COMPILER_VERSION,
        "kind": "IMAGE",
        "prompt": prompt,
        "references": references,
        "parameters": parameters,
    }
    return CompiledPrompt(prompt, content_hash(inputs), parameters)


def compile_video(snapshot: dict) -> CompiledPrompt:
    frames = actual_video_frames(snapshot["duration_seconds"])
    parameters = {**snapshot.get("video_settings", {}), "fps": 24, "num_frames": frames}
    prompt = "\n".join(
        [
            "[Video]\n" + snapshot.get("video_prompt", "").strip(),
            "[Audio]\n" + snapshot.get("audio_prompt", "").strip(),
            "[Dialogue]\n" + snapshot.get("dialogue", "").strip(),
        ]
    )
    inputs = {
        "compiler_version": COMPILER_VERSION,
        "kind": "VIDEO",
        "prompt": prompt,
        "parameters": parameters,
        "first_frame_sha256": snapshot.get("first_frame_sha256"),
    }
    return CompiledPrompt(prompt, content_hash(inputs), parameters)
