"""启动预检不执行dotenv，端口冲突不影响已有进程，收尾只关闭本次子进程。"""

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def runtime():
    spec = importlib.util.spec_from_file_location(
        "video_maker_local_runtime", ROOT / "scripts/local_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dotenv_is_data_relative_to_project_and_never_creates_data_on_check(tmp_path):
    module = runtime()
    flag = tmp_path / "should-not-execute"
    (tmp_path / ".env").write_text(f"VM_DATA_DIR='./folder $(touch {flag})/data'\n")
    config = module.load_configuration(tmp_path, {})
    assert config.data_dir.is_absolute() and "$(touch" in str(config.data_dir)
    assert str(config.data_dir).startswith(str(tmp_path))
    assert not flag.exists() and not config.data_dir.exists()
    override = module.load_configuration(tmp_path, {"VM_DATA_DIR": "./explicit-data"})
    assert override.data_dir == tmp_path / "explicit-data"


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.example:8000",
        "http://127.0.0.1:9000",
        "http://localhost:8000/v1",
        "https://localhost:8000",
    ],
)
def test_startup_rejects_backend_address_that_would_not_match_owned_server(
    tmp_path, value
):
    module = runtime()
    (tmp_path / ".env").write_text(f"VM_BACKEND_URL={value}\n")
    with pytest.raises(module.RuntimeProblem):
        module.load_configuration(tmp_path, {})


@pytest.mark.parametrize("reuse", [False, True])
def test_port_conflict_is_reported_and_existing_socket_remains_owned(reuse):
    module = runtime()
    with socket.socket() as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(module.RuntimeProblem):
            module.check_ports([port])
        assert listener.getsockname()[1] == port


def test_missing_frontend_dependencies_gives_actionable_error_without_starting(
    tmp_path,
):
    module = runtime()
    config = module.load_configuration(tmp_path, dict(os.environ))
    with pytest.raises(module.RuntimeProblem, match="npm ci"):
        module.check_dependencies(config)
    assert not config.data_dir.exists()


def test_supervisor_stops_only_children_it_started(tmp_path):
    module = runtime()
    owned = module.ManagedProcesses()
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    unrelated = subprocess.Popen(
        command,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        child = owned.spawn(command, cwd=tmp_path, env=dict(os.environ))
        owned.stop(grace_seconds=0.1)
        assert child.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_preflight_checks_actual_tools_and_build_artifact(tmp_path):
    module = runtime()
    tools = tmp_path / "bin"
    tools.mkdir()
    for name, content in {
        "node": "#!/bin/sh\necho v24.17.0\n",
        "ffprobe": "#!/bin/sh\necho ffprobe version test\n",
        "ffmpeg": "#!/bin/sh\necho reset_sar libx264 aac\n",
    }.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    (tmp_path / "backend/.venv/bin").mkdir(parents=True)
    (tmp_path / "backend/.venv/bin/python").symlink_to(sys.executable)
    (tmp_path / "frontend/node_modules/next/dist/bin").mkdir(parents=True)
    (tmp_path / "frontend/node_modules/next/dist/bin/next").write_text("// fixture")
    config = module.load_configuration(tmp_path, {"PATH": str(tools)})
    module.check_dependencies(config)
    with pytest.raises(module.RuntimeProblem, match="build"):
        module.require_build(config)
    (tmp_path / "frontend/.next").mkdir()
    (tmp_path / "frontend/.next/BUILD_ID").write_text("test")
    module.require_build(config)


def test_recently_closed_server_does_not_block_immediate_restart():
    module = runtime()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            connection, _ = listener.accept()
            connection.close()
            assert client.recv(1) == b""
    module.check_ports([port])
