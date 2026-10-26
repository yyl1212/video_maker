#!/usr/bin/env python3
"""本机生产启动与有界收尾，不启动/停止模型，不执行dotenv文本。"""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import AppSettings  # noqa: E402
from app.local_http import is_local_host  # noqa: E402


class RuntimeProblem(Exception):
    pass


@dataclass(frozen=True)
class RuntimeConfiguration:
    root: Path
    data_dir: Path
    environment: dict[str, str]

    @property
    def python(self):
        return self.root / "backend/.venv/bin/python"


def load_configuration(root: Path, environ: dict[str, str]):
    root = root.absolute()
    try:
        values = {
            k: v for k, v in dotenv_values(root / ".env").items() if v is not None
        }
        environment = {**values, **environ, "NEXT_TELEMETRY_DISABLED": "1"}
        address = urlsplit(environment.get("VM_BACKEND_URL", "http://127.0.0.1:8000"))
        if (
            address.scheme != "http"
            or address.hostname not in ("127.0.0.1", "localhost", "::1")
            or address.port != 8000
            or address.path not in ("", "/")
            or address.username
            or address.password
            or address.query
            or address.fragment
        ):
            raise ValueError("VM_BACKEND_URL")
        origins = (
            json.loads(environment["VM_FRONTEND_ORIGINS"])
            if "VM_FRONTEND_ORIGINS" in environment
            else AppSettings().frontend_origins
        )
        if not isinstance(origins, list) or "http://127.0.0.1:3000" not in origins:
            raise ValueError("origins")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or not is_local_host(parsed.netloc)
                or parsed.path != ""
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("origin")
        directory = Path(environment.get("VM_DATA_DIR", "./data")).expanduser()
        if not directory.is_absolute():
            directory = root / directory
        config = AppSettings(data_dir=directory, frontend_origins=origins)
        if config.data_dir.exists() and not config.data_dir.is_dir():
            raise ValueError("data_dir")
        environment["VM_DATA_DIR"] = str(config.data_dir)
        environment["VM_BACKEND_URL"] = "http://127.0.0.1:8000"
        environment["VM_FRONTEND_ORIGINS"] = json.dumps(origins)
        return RuntimeConfiguration(root, config.data_dir, environment)
    except (ValueError, TypeError, OSError) as exc:
        raise RuntimeProblem(
            "本机配置无效：检查数据目录、VM_FRONTEND_ORIGINS；启动脚本固定使用8000/3000端口。"
        ) from exc


def check_ports(ports):
    for port in ports:
        try:
            with socket.socket() as probe:
                # 与实际服务器一样允许重用TIME_WAIT，listen仍拒绝已在监听的端口。
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
                probe.listen()
        except OSError as exc:
            raise RuntimeProblem(
                f"端口{port}已占用或不可绑定，请先停止对应服务；不会终止已有进程。"
            ) from exc


def tool(config, name, arguments):
    executable = shutil.which(name, path=config.environment.get("PATH"))
    if not executable:
        raise RuntimeProblem(f"缺少{name}，请先安装并加入PATH。")
    try:
        result = subprocess.run(
            [executable, *arguments],
            env=config.environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=8,
        )
        return result.stdout + result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeProblem(f"{name}检查失败，请核对安装。") from exc


def check_dependencies(config):
    if sys.version_info[:2] != (3, 13):
        raise RuntimeProblem("本版使用Python 3.13，请重新创建backend环境。")
    if (
        not config.python.is_file()
        or not (config.root / "frontend/node_modules/next/dist/bin/next").is_file()
    ):
        raise RuntimeProblem(
            "依赖尚未准备：backend执行uv sync --locked；frontend执行npm ci。"
        )
    node = tool(config, "node", ["--version"])
    version_match = re.search(r"v(\d+)\.", node)
    if not version_match or int(version_match[1]) < 24:
        raise RuntimeProblem("需要Node.js 24或更高版本。")
    scale = tool(config, "ffmpeg", ["-hide_banner", "-h", "filter=scale"])
    encoders = tool(config, "ffmpeg", ["-hide_banner", "-encoders"])
    if (
        "reset_sar" not in scale
        or "libx264" not in encoders
        or not re.search(r"\baac\b", encoders)
    ):
        raise RuntimeProblem(
            "FFmpeg须支持scale reset_sar、libx264和AAC，请升级完整版本。"
        )
    tool(config, "ffprobe", ["-version"])


def require_build(config):
    if not (config.root / "frontend/.next/BUILD_ID").is_file():
        raise RuntimeProblem("缺少生产构建，请使用scripts/start-local.sh --build。")


class ManagedProcesses:
    def __init__(self):
        self.children = []

    def spawn(self, arguments, *, cwd, env):
        child = subprocess.Popen(arguments, cwd=cwd, env=env, start_new_session=True)
        self.children.append(child)
        return child

    def stop(self, grace_seconds=30):
        children, self.children = self.children, []
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace_seconds
        while any(p.poll() is None for p in children) and time.monotonic() < deadline:
            time.sleep(0.05)
        for child in children:
            # 即使组长已退出，也清理本次启动的同组残留子进程。
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)


def wait_ready(child, url):
    deadline = time.monotonic() + 30
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeProblem("服务启动失败，请检查上方日志。")
        try:
            with opener.open(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    raise RuntimeProblem("本机服务未在30秒内就绪，请检查上方日志。")


def request_stop(*_):
    raise KeyboardInterrupt()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="映序本机生产启动（8000/3000），模型服务请自行启动。"
    )
    parser.add_argument(
        "--check", action="store_true", help="只检查依赖、配置和端口，不创建数据库"
    )
    parser.add_argument(
        "--build", action="store_true", help="在540秒总截止内构建前端后启动"
    )
    args = parser.parse_args(argv)
    if args.check and args.build:
        parser.error("--check与--build不能同时使用")
    owned = ManagedProcesses()
    try:
        config = load_configuration(ROOT, dict(os.environ))
        check_dependencies(config)
        check_ports([8000, 3000])
        if args.check:
            print("先决条件、配置及端口检查通过；未创建数据库，未访问模型。")
            if not (ROOT / "frontend/.next/BUILD_ID").is_file():
                print("首次运行请使用 --build 创建生产构建。")
            return 0
        if args.build:
            subprocess.run(
                [
                    str(config.python),
                    str(ROOT / "scripts/run-check.py"),
                    "--timeout",
                    "540",
                    "--cwd",
                    str(ROOT / "frontend"),
                    "--",
                    "node",
                    "node_modules/next/dist/bin/next",
                    "build",
                ],
                env=config.environment,
                check=True,
            )
            check_ports([8000, 3000])
        require_build(config)
        signal.signal(signal.SIGTERM, request_stop)
        backend = owned.spawn(
            [
                str(config.python),
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--timeout-graceful-shutdown",
                "15",
            ],
            cwd=ROOT / "backend",
            env=config.environment,
        )
        wait_ready(backend, "http://127.0.0.1:8000/api/v1/health")
        frontend = owned.spawn(
            [
                "node",
                "node_modules/next/dist/bin/next",
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3000",
            ],
            cwd=ROOT / "frontend",
            env=config.environment,
        )
        wait_ready(frontend, "http://127.0.0.1:3000/")
        print(
            f"映序已启动：http://127.0.0.1:3000  数据目录：{config.data_dir}",
            flush=True,
        )
        while all(child.poll() is None for child in owned.children):
            time.sleep(0.2)
        raise RuntimeProblem("一个服务已退出，正在关闭本次启动的另一服务。")
    except KeyboardInterrupt:
        print("\n正在停止本次启动的前后端，模型服务保持独立。", flush=True)
        return 0
    except (RuntimeProblem, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            owned.stop()
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
