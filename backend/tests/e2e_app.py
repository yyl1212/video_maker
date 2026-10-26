"""Playwright专用服务入口；不进入生产工厂、不接受任意数据目录。"""

from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from workflow_fixtures import FixtureModels, install_models

from app.config import AppSettings
from app.main import create_app


def create_fixture_app():
    directory = TemporaryDirectory(prefix="video-maker-browser-")
    app = create_app(AppSettings(data_dir=Path(directory.name)))
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        try:
            async with original(instance):
                install_models(
                    instance,
                    FixtureModels(image_delay=3, video_delay=2, text_delay=0.2),
                )
                yield
        finally:
            directory.cleanup()

    app.router.lifespan_context = lifespan
    return app
