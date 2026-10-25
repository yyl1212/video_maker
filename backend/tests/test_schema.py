"""保护新库启动、真实外键与全局执行槽，而非测试 ORM 的普通取值。"""

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def database_at(tmp_path):
    from app.config import AppSettings
    from app.db import Database

    database = Database(AppSettings(data_dir=tmp_path / "new-workspace"))
    database.initialize()
    return database


def test_empty_database_starts_without_old_project_files(tmp_path):
    database = database_at(tmp_path)
    try:
        with database.session_scope() as session:
            assert session.scalar(text("SELECT count(*) FROM projects")) == 0
            assert session.scalar(text("SELECT state FROM runtime_state")) == "IDLE"
            assert session.scalar(text("SELECT version FROM model_settings")) == 1
        database.initialize()
        assert (tmp_path / "new-workspace" / "app.sqlite3").is_file()
    finally:
        database.close()


def test_schema_has_no_fk_cycle_and_migration_roundtrips(tmp_path):
    from app.models import Base

    expected = {
        "projects",
        "episodes",
        "shots",
        "shot_reference_assets",
        "shot_selections",
        "assets",
        "tasks",
        "runtime_state",
        "model_settings",
    }
    database = database_at(tmp_path)
    try:
        assert {table.name for table in Base.metadata.sorted_tables} == expected
        assert expected <= set(inspect(database.engine).get_table_names())
        database.migrate("base")
        assert "projects" not in inspect(database.engine).get_table_names()
        database.migrate("head")
        assert expected <= set(inspect(database.engine).get_table_names())
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        database.close()


def test_foreign_keys_enabled_on_every_connection(tmp_path):
    from app.models import Episode

    database = database_at(tmp_path)
    try:
        with database.engine.connect() as first, database.engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert (
                    connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
                )
                assert (
                    connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
                )
        with (
            pytest.raises(IntegrityError),
            database.session_scope(write=True) as session,
        ):
            session.add(Episode(project_id=999, title="不能引用不存在的项目"))
            session.flush()
    finally:
        database.close()


def test_pending_and_running_share_one_global_slot(tmp_path):
    from app.models import Episode, Project, Task

    database = database_at(tmp_path)
    try:
        with database.session_scope(write=True) as session:
            project = Project(name="第一部")
            session.add(project)
            session.flush()
            episode = Episode(project_id=project.id, title="第一集")
            session.add(episode)
            session.flush()
            project_id, episode_id = project.id, episode.id

        def new_task(key, status):
            return Task(
                project_id=project_id,
                episode_id=episode_id,
                kind="PARSE_SCRIPT",
                status=status,
                idempotency_key=key,
                request_hash="a" * 64,
                input_hash="b" * 64,
                input_snapshot={},
            )

        with database.session_scope(write=True) as session:
            session.add(new_task("first", "PENDING"))
        with (
            pytest.raises(IntegrityError),
            database.session_scope(write=True) as session,
        ):
            session.add(new_task("second", "RUNNING"))
            session.flush()
        with database.session_scope(write=True) as session:
            session.execute(text("UPDATE tasks SET status='FAILED'"))
            session.add(new_task("second", "RUNNING"))
    finally:
        database.close()


def test_checks_timeout_terminates_process_group(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "run-check.py"
    marker = tmp_path / "child-finished"
    child = f"import time,pathlib;time.sleep(1.3);pathlib.Path({str(marker)!r}).touch()"
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(20)"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--timeout",
            "0.3",
            "--",
            sys.executable,
            "-c",
            parent,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 124
    assert "超时" in result.stderr
    subprocess.run([sys.executable, "-c", "import time;time.sleep(1.5)"], timeout=3)
    assert not marker.exists()
