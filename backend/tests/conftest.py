import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from app.config import AppSettings
    from app.main import create_app

    app = create_app(AppSettings(data_dir=tmp_path / "data"))
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        token = http.get("/api/v1/bootstrap").json()["token"]
        http.headers["X-Local-Token"] = token
        yield http


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "self-made.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=olive:s=96x64:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        timeout=15,
        capture_output=True,
    )
    return path


@pytest.fixture
def engine(tmp_path):
    from task_helpers import SampleHandler, records

    from app.config import AppSettings
    from app.db import Database
    from app.services.storage import StorageService
    from app.services.tasks import TaskService

    config = AppSettings(data_dir=tmp_path / "data")
    db = Database(config)
    db.initialize()
    ids = records(db)
    handler = SampleHandler()
    service = TaskService(
        db,
        StorageService(config.data_dir),
        {"OPTIMIZE_PROMPTS": handler, "PARSE_SCRIPT": handler},
    )
    yield service, ids, handler
    db.close()
