"""独立短Session、明确事务边界与逐连接SQLite约束。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .config import AppSettings
from .errors import AppError
from .models import ModelSettings, RuntimeState
from .schemas.clients import ImageSettings, TextSettings, VideoSettings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Database:
    def __init__(self, settings: AppSettings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{settings.database_path}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )

        @event.listens_for(self.engine, "connect")
        def connect(connection, record):
            # 由SQLAlchemy显式发BEGIN，绕过sqlite3各版本的隐式事务差异。
            connection.isolation_level = None
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            mode = (
                "BEGIN IMMEDIATE"
                if connection.get_execution_options().get("sqlite_write")
                else "BEGIN"
            )
            connection.exec_driver_sql(mode)

    def migrate(self, revision: str = "head") -> None:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        with self.engine.connect().execution_options(sqlite_write=True) as connection:
            config.attributes["connection"] = connection
            if revision == "base":
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)

    def initialize(self) -> None:
        self.migrate()
        with self.session_scope(write=True) as session:
            if session.get(RuntimeState, 1) is None:
                session.add(RuntimeState(id=1))
            if session.get(ModelSettings, 1) is None:
                session.add(
                    ModelSettings(
                        id=1,
                        text_settings=TextSettings().model_dump(),
                        image_settings=ImageSettings().model_dump(),
                        video_settings=VideoSettings().model_dump(),
                    )
                )

    @contextmanager
    def session_scope(self, *, write: bool = False):
        try:
            with self.engine.connect().execution_options(
                sqlite_write=write
            ) as connection:
                with (
                    Session(connection, expire_on_commit=False) as session,
                    session.begin(),
                ):
                    yield session
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", 0)
            busy = code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            raise AppError(
                503,
                "DATABASE_BUSY" if busy else "DATABASE_UNAVAILABLE",
                "数据库正忙，请稍后重试" if busy else "数据库暂时不可用，请稍后重试",
            ) from error

    def close(self) -> None:
        self.engine.dispose()
