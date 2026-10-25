import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from task_helpers import command

from app.config import AppSettings
from app.db import Database
from app.errors import AppError
from app.models import RuntimeState, Task
from app.services.tasks import TaskService, register_submission


def test_two_connections_accept_only_one_task(engine):
    service, ids, _ = engine
    second_db = Database(AppSettings(data_dir=service.storage.root))
    second = TaskService(second_db, service.storage, service.handlers)
    barrier = Barrier(2)

    def submit(current):
        barrier.wait(timeout=2)
        try:
            return current.submit(command(*ids)).task.status
        except AppError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (service, second)))
    second_db.close()
    assert sorted(map(str, results)) == ["429", "PENDING"]
    with service.database.session_scope() as session:
        assert session.get(RuntimeState, 1).state == "ACTIVE"


def test_database_busy_is_503_not_task_busy(engine):
    service, ids, _ = engine
    import sqlite3

    from sqlalchemy import event

    connection = sqlite3.connect(service.database.engine.url.database, timeout=0.05)
    connection.execute("BEGIN IMMEDIATE")

    def short_timeout(dbapi_connection, record, proxy):
        dbapi_connection.execute("PRAGMA busy_timeout=40")

    event.listen(service.database.engine, "checkout", short_timeout)
    try:
        with pytest.raises(AppError) as error:
            service.submit(command(*ids))
        assert error.value.status == 503
        assert error.value.code == "DATABASE_BUSY"
    finally:
        connection.rollback()
        connection.close()
        event.remove(service.database.engine, "checkout", short_timeout)


def test_cross_endpoint_kinds_share_slot(engine):
    service, ids, _ = engine
    service.submit(command(*ids, kind="PARSE_SCRIPT"))
    with pytest.raises(AppError) as error:
        service.submit(command(*ids))
    assert error.value.status == 429
    assert error.value.message == "当前有任务执行中"
    assert error.value.details["active_job_id"] > 0


def test_same_key_reuses_job_without_second_dispatch(engine):
    service, ids, handler = engine
    data = command(*ids)
    first, second = service.submit(data), service.submit(data)
    assert first.created and not second.created
    assert first.task.id == second.task.id
    background = BackgroundTasks()
    register_submission(background, first)
    register_submission(background, second)
    asyncio.run(background())
    assert handler.calls == [first.task.id]
    # 已完成后仍复用原任务，不检查已变化的目标版本或再次执行。
    assert service.submit(data).task.status == "DONE"


def test_same_key_different_body_conflicts(engine):
    service, ids, _ = engine
    key = str(uuid4())
    service.submit(command(*ids, key=key))
    with pytest.raises(AppError) as error:
        service.submit(command(*ids, key=key, prompt="不同输入"))
    assert error.value.status == 409


def test_submission_scope_versions_and_registration_failure(engine, monkeypatch):
    service, ids, _ = engine
    with pytest.raises(AppError) as error:
        service.submit(command(*ids).model_copy(update={"expected_shot_version": 99}))
    assert error.value.status == 409
    result = service.submit(command(*ids))
    background = BackgroundTasks()

    def broken(*args, **kwargs):
        raise RuntimeError("registration")

    monkeypatch.setattr(background, "add_task", broken)
    with pytest.raises(AppError):
        register_submission(background, result)
    assert service.get(result.task.id).status == "FAILED"
    with service.database.session_scope() as session:
        assert session.get(RuntimeState, 1).state == "IDLE"
        assert (
            session.get(Task, result.task.id).error_code
            == "BACKGROUND_REGISTRATION_FAILED"
        )
