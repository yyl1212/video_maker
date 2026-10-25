"""在multipart解析前限制完整请求体，包括无Content-Length的分块请求。"""

import re

from starlette.responses import JSONResponse

MAX_MULTIPART_BYTES = 512 * 1024 * 1024 + 64 * 1024


def is_upload_request(method: str, path: str) -> bool:
    return method == "POST" and bool(
        re.fullmatch(r"/api/v1/projects/[1-9][0-9]*/assets", path)
    )


class UploadTooLarge(Exception):
    pass


class UploadBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_upload_request(
            scope["method"], scope["path"]
        ):
            return await self.app(scope, receive, send)

        response = JSONResponse(
            {
                "error": {
                    "code": "UPLOAD_TOO_LARGE",
                    "message": "上传请求过大，视频最多512 MiB",
                }
            },
            status_code=413,
        )
        lengths = [v for k, v in scope["headers"] if k.lower() == b"content-length"]
        if lengths and (
            len(lengths) != 1
            or not lengths[0].isdigit()
            or int(lengths[0]) > MAX_MULTIPART_BYTES
        ):
            return await response(scope, receive, send)
        total, exceeded, replaced = 0, False, False

        async def limited_receive():
            nonlocal total, exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > MAX_MULTIPART_BYTES:
                    exceeded = True
                    raise UploadTooLarge()
            return message

        async def limited_send(message):
            nonlocal replaced
            # FastAPI可能把解析异常转成400；超过硬上限始终返回统一的413。
            if exceeded:
                if not replaced:
                    replaced = True
                    await response(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except UploadTooLarge:
            if not replaced:
                await response(scope, receive, send)
