"""独占工作区后恢复；未知上游状态不能被心跳或连通性探测解锁。"""

import asyncio
import fcntl
import os
import signal
import time
from datetime import timedelta
from pathlib import Path

import psutil
from sqlalchemy import select

from app.errors import AppError
from app.models import RuntimeState, Task
from app.models.base import utc_now
from app.schemas.tasks import RuntimeRead
from app.services.projects import check_version


class WorkspaceLock:
    def __init__(self, data_dir: Path):
        self.root, self.descriptor = data_dir, None

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.root / ".workspace.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(descriptor)
                raise
        except OSError as exc:
            raise AppError(
                503,
                "WORKSPACE_IN_USE",
                "此数据目录已有后端实例运行，或无法取得工作区锁",
            ) from exc
        self.descriptor = descriptor
        return self

    def release(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


class ProcessGuard:
    """只使用进程身份信息，不读取内存或做容量门禁。"""

    def __init__(self, storage):
        self.storage = storage

    def capture(self, pid, task_id, token, directory):
        directory = self.storage.checked_path(
            Path(directory), self.storage.root / "jobs"
        )
        process = psutil.Process(pid)
        if os.getpgid(pid) != pid or Path(process.cwd()) != directory:
            raise AppError(503, "PROCESS_IDENTITY_UNKNOWN", "无法核验媒体进程身份")
        return {
            "pid": pid,
            "pgid": pid,
            "create_time": process.create_time(),
            "task_id": task_id,
            "token": token,
            "directory": str(directory),
        }

    @staticmethod
    def _group_gone(pgid):
        try:
            os.killpg(pgid, 0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    def stop(self, identity, task_id) -> bool:
        if not identity:
            return False
        try:
            pid, pgid = identity["pid"], identity["pgid"]
            if (
                not isinstance(pid, int)
                or pid <= 1
                or pgid != pid
                or identity["task_id"] != task_id
            ):
                return False
            if self._group_gone(pgid):
                return True
            directory = self.storage.checked_path(
                Path(identity["directory"]), self.storage.root / "jobs"
            )
            if directory != self.storage.root / "jobs" / str(task_id):
                return False
            marker = self.storage.checked_file(directory / ".process-token")
            if marker.read_text() != identity["token"]:
                return False
            try:
                root = psutil.Process(pid)
                if abs(root.create_time() - identity["create_time"]) > 0.001:
                    return False
            except psutil.NoSuchProcess:
                pass
            members = []
            for identifier in psutil.pids():
                try:
                    if os.getpgid(identifier) != pgid:
                        continue
                    member = psutil.Process(identifier)
                    if member.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                        continue
                    if (
                        member.create_time() < identity["create_time"] - 0.001
                        or Path(member.cwd()) != directory
                    ):
                        return False
                    members.append(member)
                except (ProcessLookupError, psutil.NoSuchProcess):
                    continue
            if not members:
                return True
            os.killpg(pgid, signal.SIGKILL)
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if all(
                    not p.is_running()
                    or p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
                    for p in members
                ):
                    return True
                time.sleep(0.005)
            return False
        except (KeyError, TypeError, ValueError, OSError, psutil.Error, AppError):
            return False


class RecoveryService:
    def __init__(self, service):
        self.service = service
        self.guard = ProcessGuard(service.storage)

    def inspect_execution(self, task):
        remote = task.execution_json.get("remote", {})
        local = task.execution_json.get("local", {})
        remote_unknown = bool(remote.get("started") and not remote.get("finished"))
        local_unknown = False
        if local.get("started") and not local.get("finished"):
            stopped = self.guard.stop(local.get("identity"), task.id)
            local_unknown = not stopped
            if stopped:
                task.execution_json = {
                    **task.execution_json,
                    "local": {**local, "finished": True},
                }
        backend = (
            remote.get("backend", "local")
            if remote_unknown
            else ("ffmpeg" if local_unknown else None)
        )
        return remote_unknown or local_unknown, backend

    def startup(self) -> None:
        with self.service.database.session_scope(write=True) as session:
            runtime = session.get(RuntimeState, 1)
            interrupted = list(
                session.scalars(
                    select(Task).where(Task.status.in_(("PENDING", "RUNNING")))
                )
            )
            for task in interrupted:
                uncertain, backend = (
                    self.inspect_execution(task)
                    if task.status == "RUNNING"
                    else (False, None)
                )
                task.status, task.stage = "FAILED", "应用已中断"
                task.error_code, task.error_message = (
                    "APP_INTERRUPTED",
                    "上次任务因应用停止而中断，不会自动重发",
                )
                task.finished_at = utc_now()
                if runtime.state != "NEEDS_RESET":
                    self.service.release(runtime, task.id, uncertain, backend)
            if not interrupted and runtime.state == "ACTIVE":
                runtime.state, runtime.affected_backend = "NEEDS_RESET", "local"
                runtime.reason = "任务与运行记录不一致，请核对本机执行已停止后恢复"
                runtime.version += 1

    def inspect_pending(self) -> None:
        self.service.runner.retry_finalizations()
        now = utc_now()
        with self.service.database.session_scope(write=True) as session:
            runtime = session.get(RuntimeState, 1)
            active = session.scalar(
                select(Task).where(Task.status.in_(("PENDING", "RUNNING")))
            )
            if active is None:
                return
            if active.status == "PENDING" and active.created_at < now - timedelta(
                seconds=self.service.policy.pending_seconds
            ):
                active.status, active.stage = "FAILED", "任务未启动"
                active.error_code, active.error_message = (
                    "BACKGROUND_NOT_STARTED",
                    "后台任务未能启动，请重新提交",
                )
                active.finished_at = now
                if runtime.state != "NEEDS_RESET":
                    self.service.release(runtime, active.id)
            elif active.status == "RUNNING":
                stale = (
                    not active.heartbeat_at
                    or active.heartbeat_at
                    < now - timedelta(seconds=self.service.policy.stale_seconds)
                )
                warning = "执行心跳异常，仍保留任务占位，请检查服务" if stale else ""
                if runtime.state == "ACTIVE" and runtime.reason != warning:
                    runtime.reason = warning
                    runtime.version += 1

    def confirm_reset(self, expected_version: int, backend: str) -> RuntimeRead:
        with (
            self.service.gate.lock,
            self.service.database.session_scope(write=True) as session,
        ):
            runtime = session.get(RuntimeState, 1)
            check_version(runtime, expected_version)
            if runtime.state != "NEEDS_RESET" or runtime.affected_backend != backend:
                raise AppError(
                    409, "RESET_SCOPE_CHANGED", "执行状态或受影响服务已变化，请重新核对"
                )
            if self.service.gate.running:
                raise AppError(
                    409, "RUNNER_STILL_ACTIVE", "本机任务尚未退出，不能恢复执行槽"
                )
            if self.service.gate.poisoned:
                raise AppError(
                    503,
                    "DATABASE_UNAVAILABLE",
                    "任务记录尚未写入，请恢复数据库或重启应用",
                )
            task = (
                session.get(Task, runtime.active_task_id)
                if runtime.active_task_id
                else None
            )
            if task:
                local = task.execution_json.get("local", {})
                if local.get("started") and not local.get("finished"):
                    if not self.guard.stop(local.get("identity"), task.id):
                        raise AppError(
                            503,
                            "PROCESS_STATE_UNKNOWN",
                            "无法确认原媒体进程已停止，请先处理残留进程",
                        )
                    task.execution_json = {
                        **task.execution_json,
                        "local": {**local, "finished": True},
                    }
            runtime.state, runtime.active_task_id = "IDLE", None
            runtime.reason, runtime.affected_backend = "", None
            runtime.version += 1
            session.flush()
            return RuntimeRead.model_validate(runtime)

    async def watch(self):
        while True:
            await asyncio.sleep(self.service.policy.sweep_seconds)
            try:
                await self.service.runner.io(self.inspect_pending)
            except AppError:
                # 数据库不可用时不释放任何槽位，下次只重试元数据巡检。
                continue
