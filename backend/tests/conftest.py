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
