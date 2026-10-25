#!/usr/bin/env python3
"""对正在运行的本机生产服务验证原始Host边界，不连接外部域名。"""

import argparse
import http.client
from urllib.parse import urlsplit


def target(value):
    parts = urlsplit(value)
    if parts.scheme != "http" or parts.hostname not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise argparse.ArgumentTypeError("验收目标必须为本机HTTP服务")
    return parts


def check(address, path, host, expected, forwarded=None):
    headers = {"Host": host, "Sec-Fetch-Site": "same-origin"}
    if forwarded:
        headers["X-Forwarded-Host"] = forwarded
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.status == expected, (
            f"{path} Host={host} 应为{expected}，实际{response.status}"
        )
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", type=target, default="http://127.0.0.1:3000")
    parser.add_argument("--backend", type=target, default="http://127.0.0.1:8000")
    args = parser.parse_args()
    for path in ("/api/v1/bootstrap", "/api/v1/projects", "/api/v1/episodes/1"):
        check(args.frontend, path, "external.example:3000", 403)
        check(args.frontend, path, "external.example:3000", 403, "127.0.0.1:3000")
        check(args.backend, path, "127.0.0.1:8000", 403, "external.example:3000")
    check(args.frontend, "/api/v1/bootstrap", "127.0.0.1:3000", 200)
    check(args.backend, "/api/v1/bootstrap", "127.0.0.1:8000", 200)
    print("真实Next→FastAPI链路：外部Host及伪造转发头均拒绝，合法回环访问通过。")


if __name__ == "__main__":
    main()
