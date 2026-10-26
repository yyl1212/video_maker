"""本机运行配置；可配置连接，不做内存探测。"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VM_", extra="ignore")

    data_dir: Path = PROJECT_ROOT / "data"
    frontend_origins: list[str] = [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        value = value.expanduser()
        return (
            (PROJECT_ROOT / value).resolve()
            if not value.is_absolute()
            else value.resolve()
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.sqlite3"
