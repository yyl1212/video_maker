#!/usr/bin/env python3
"""真实本机链路验收。须对使用隔离VM_DATA_DIR启动的服务执行，会创建原创样片项目。"""

import argparse
import hashlib
import json
import struct
import subprocess
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    address = urlsplit(args.frontend)
    if address.scheme != "http" or address.hostname not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise ValueError("验收目标须为本机HTTP地址")
    origin = f"{address.scheme}://{address.netloc}"
    token = ""

    def request(method, path, body=None, content_type="application/json", headers=None):
        extra = headers or {}
        req = Request(
            origin + "/api/v1" + path,
            method=method,
            data=body,
            headers={
                "X-Local-Token": token,
                "Content-Type": content_type,
                "Origin": origin,
                **extra,
            },
        )
        try:
            with urlopen(req, timeout=45) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            raise AssertionError(
                f"{method} {path}: {exc.code} {exc.read()[:300]!r}"
            ) from exc

    def api(method, path, body=None):
        _, _, raw = request(
            method, path, json.dumps(body).encode() if body is not None else None
        )
        return json.loads(raw)

    def upload(project_id, name, content, mime):
        boundary = "video-maker-self-made-fixture"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        body += content + f"\r\n--{boundary}--\r\n".encode()
        return json.loads(
            request(
                "POST",
                f"/projects/{project_id}/assets",
                body,
                f"multipart/form-data; boundary={boundary}",
            )[2]
        )

    token = api("GET", "/bootstrap")["token"]
    project = api(
        "POST",
        "/projects",
        {
            "name": "清晨来信 · 原创验收",
            "style_anchor": "暖色自然光，安静的清晨，克制的画面",
            "characters": [
                {"id": "lin", "name": "小林", "description": "短发，米色外套"}
            ],
        },
    )
    episode = api(
        "POST",
        f"/projects/{project['id']}/episodes",
        {
            "title": "第一集 · 门前",
            "script_text": "清晨，小林推开门，发现门前的一封信。小林：早上好。",
        },
    )
    image = Image.new("RGB", (384, 672), "#e6dcc6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((72, 120, 312, 600), fill="#6b785b")
    draw.rectangle((98, 148, 286, 600), fill="#bba78b")
    draw.ellipse((252, 380, 265, 393), fill="#455743")
    draw.rectangle((154, 570, 230, 616), fill="#fff8e7", outline="#8b7a5f", width=2)
    stream = BytesIO()
    image.save(stream, "PNG")
    frame = upload(project["id"], "original-door.png", stream.getvalue(), "image/png")
    with TemporaryDirectory(prefix="vm-m3-") as directory:
        video = Path(directory) / "original.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x6b785b:s=384x672:r=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "0.75",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        # 合法MP4 free盒，确保真实HTTP体超过Next默认10MiB。
        with video.open("ab") as target:
            target.write(struct.pack(">I4s", 12 * 1024 * 1024 + 8, b"free"))
            target.write(bytes(12 * 1024 * 1024))
        original = video.read_bytes()
        clip = upload(project["id"], "original-sound.mp4", original, "video/mp4")
    assert clip["size_bytes"] > 10 * 1024 * 1024
    assert clip["sha256"] == hashlib.sha256(original).hexdigest()
    assert clip["has_audio"] and clip["audio_sample_rate"] == 48000
    status, headers, data = request(
        "GET",
        f"/assets/{clip['id']}/content?project_id={project['id']}",
        headers={"Range": "bytes=0-31"},
    )
    assert (
        status == 206
        and len(data) == 32
        and headers["Content-Range"].startswith("bytes 0-31/")
    )
    shot = api(
        "POST",
        f"/episodes/{episode['id']}/shots",
        {
            "raw_description": "小林推开木门，俯身拾起门前的信",
            "character_ids": ["lin"],
            "image_prompt": "清晨的木门与一封信，人物身着米色外套，柔和侧光",
            "video_prompt": "人物缓缓推门，低头看见信封，镜头固定",
            "audio_prompt": "轻微门轴声与晨鸟",
            "dialogue": "早上好。",
            "duration_seconds": 3,
        },
    )
    shot = api(
        "PUT",
        f"/shots/{shot['id']}/references",
        {
            "expected_version": shot["version"],
            "references": [{"asset_id": frame["id"], "role": "scene"}],
        },
    )
    for field, asset in (
        ("first_frame_asset_id", frame),
        ("selected_video_asset_id", clip),
    ):
        shot = api(
            "PUT",
            f"/shots/{shot['id']}/selection",
            {
                "shot_version": shot["version"],
                "selection_version": shot["selection"]["version"],
                field: asset["id"],
                "confirm_uploaded": True,
            },
        )
    assert shot["video_ready"] and not shot["selection"]["video_stale"]
    changed = api(
        "PATCH",
        f"/shots/{shot['id']}",
        {
            "expected_version": shot["version"],
            "audio_prompt": "远处的脚步声",
        },
    )
    assert not changed["selection"]["first_frame_stale"]
    assert changed["selection"]["video_stale"]
    assert changed["selection"]["selected_video_asset_id"] == clip["id"]
    print(
        "真实Next→FastAPI：>10MiB原MP4完整入库，SHA一致、音轨保留、Range206，参考绑定/双版本采用/过期保留通过。"
    )
    print(f"演示页面：{origin}/projects/{project['id']}/episodes/{episode['id']}")


if __name__ == "__main__":
    main()
