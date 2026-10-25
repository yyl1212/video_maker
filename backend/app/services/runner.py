"""响应后执行、固定截止与失败封闭；短数据库操作不占用事件循环。"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.errors import AppError, BackendError
from app.models import Task
from app.models.base import utc_now
from app.services.recovery import ProcessGuard
from app.services.tasks import InputAsset, TaskSnapshot

logger = logging.getLogger(__name__)

# 先等待许可；父进程在持久化身份前崩溃会产生EOF，启动器不会执行真正程序。
LAUNCHER = (
    "import os,sys; os.execvpe(sys.argv[1],sys.argv[1:],os.environ) "
    "if os.read(0,1)==b'G' else sys.exit(0)"
)


@dataclass(frozen=True)
class ProcessResult:
    stdout_path: Path
    stderr_path: Path
    returncode: int


class TaskContext:
    def __init__(self, service, task_id, snapshot, deadline):
        self.service, self.task_id, self.snapshot = service, task_id, snapshot
        self.frozen_assets = ()
        self.deadline = deadline
        self._deadline = asyncio.get_running_loop().time() + max(
            0, (deadline - utc_now()).total_seconds()
        )
        self.stop_requested = False
        self._process_active = False
        self.job_dir = service.storage.checked_path(
            service.storage.root / "jobs" / str(task_id)
        )
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def remaining(self) -> float:
        return max(0, self._deadline - asyncio.get_running_loop().time())

    def ensure_open(self):
        if self.stop_requested or self.remaining() <= 0:
            raise AppError(409, "DEADLINE_EXCEEDED", "任务已停止或超过固定总截止")

    async def report(self, stage: str, progress: float | None = None):
        self.ensure_open()
        await self.service.runner.io(
            self.service.running_update, self.task_id, stage=stage, progress=progress
        )

    async def mark_remote_started(self, backend: str):
        self.ensure_open()
        if backend not in ("llm", "drawthings", "mlx"):
            raise AppError(422, "UNKNOWN_BACKEND", "本机模型类型不合法")
        await self.service.runner.io(
            self.service.running_update,
            self.task_id,
            execution={
                "remote": {"backend": backend, "started": True, "finished": False},
            },
        )

    async def mark_remote_finished(self):
        self.ensure_open()

        def load():
            with self.service.database.session_scope() as session:
                task = session.get(Task, self.task_id)
                return dict(task.execution_json.get("remote", {}))

        remote = await self.service.runner.io(load)
        if not remote.get("started"):
            raise AppError(409, "REMOTE_NOT_STARTED", "没有已开始的远程执行记录")
        await self.service.runner.io(
            self.service.running_update,
            self.task_id,
            execution={
                "remote": {**remote, "finished": True},
            },
        )

    async def run_process(self, arguments: list[str]) -> ProcessResult:
        self.ensure_open()
        if self._process_active:
            raise AppError(409, "PROCESS_BUSY", "本任务已有媒体进程执行中")
        self._process_active = True
        token = uuid4().hex
        (self.job_dir / ".process-token").write_text(token)
        stdout_path, stderr_path = (
            self.job_dir / f"{token}.{ext}" for ext in ("stdout", "stderr")
        )
        process, identity, stopped = None, None, False
        guard = ProcessGuard(self.service.storage)
        try:
            await self.service.runner.io(
                self.service.running_update,
                self.task_id,
                execution={
                    "local": {"started": False, "finished": False},
                },
            )
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    LAUNCHER,
                    *arguments,
                    cwd=self.job_dir,
                    start_new_session=True,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                )
                identity = await self.service.runner.io(
                    guard.capture, process.pid, self.task_id, token, self.job_dir
                )
                await self.service.runner.io(
                    self.service.running_update,
                    self.task_id,
                    execution={
                        "local": {
                            "started": True,
                            "finished": False,
                            "identity": identity,
                        },
                    },
                )
                self.ensure_open()
                process.stdin.write(b"G")
                await process.stdin.drain()
                process.stdin.close()
                await process.wait()
                stopped = await self.service.runner.io(
                    guard.stop, identity, self.task_id
                )
                if not stopped:
                    raise BackendError(
                        "PROCESS_STATE_UNKNOWN", "无法确认媒体进程组已停止", True
                    )
                if process.returncode:
                    raise AppError(
                        422,
                        "MEDIA_PROCESS_FAILED",
                        "媒体处理未完成，请检查输入或FFmpeg",
                    )
                return ProcessResult(stdout_path, stderr_path, process.returncode)
        finally:
            if process:
                process.stdin.close()
                if identity and not stopped:
                    stopped = await self.service.runner.io(
                        guard.stop, identity, self.task_id
                    )
                if stopped or identity is None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1)
                    except TimeoutError:
                        stopped = False
                if stopped:
                    await self.service.runner.io(
                        self.service.running_update,
                        self.task_id,
                        execution={
                            "local": {
                                "started": True,
                                "finished": True,
                                "identity": identity,
                            },
                        },
                    )
            self._process_active = False


class TaskRunner:
    def __init__(self, service):
        self.service = service
        self._live = {}
        self._runs = set()
        self._pending_finalizations = {}
        self._io_tasks = set()

    async def io(self, function, *args, **kwargs):
        """取消等待不代表线程已经退出；保留真实线程任务直到副作用完成。"""
        operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self._io_tasks.add(operation)

        def completed(task):
            self._io_tasks.discard(task)
            if not task.cancelled():
                task.exception()

        operation.add_done_callback(completed)
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # 保证一次正常取消不会留下仍在写数据库的后台线程。
            await asyncio.shield(operation)
            raise

    async def heartbeat(self, task_id):
        while True:
            await asyncio.sleep(self.service.policy.heartbeat_seconds)
            try:
                await self.io(self.service.running_update, task_id)
            except AppError:
                logger.error("任务心跳写入失败：%s", task_id)

    async def run(self, task_id: int) -> None:
        current = asyncio.current_task()
        self._runs.add(current)
        heartbeat, worker, context, outcome = None, None, None, None
        finalized = False
        owned = False
        try:
            claim = asyncio.create_task(self.io(self.service.claim, task_id))
            interrupted = False
            try:
                claimed = await asyncio.shield(claim)
            except asyncio.CancelledError:
                claimed = await asyncio.shield(claim)
                interrupted = True
            if claimed is None:
                if interrupted:
                    await self.io(
                        self.service.fail_pending,
                        task_id,
                        "APP_INTERRUPTED",
                        "应用已停止，任务未自动重试",
                    )
                return
            owned = True
            if interrupted:
                raise asyncio.CancelledError()
            kind, input_hash, stored, deadline = claimed
            heartbeat = asyncio.create_task(self.heartbeat(task_id))
            snapshot = TaskSnapshot(
                data=stored["data"],
                input_hash=input_hash,
                assets=tuple(InputAsset(**item) for item in stored["assets"]),
                compiler_version=stored["compiler_version"],
            )
            context = TaskContext(self.service, task_id, snapshot, deadline)

            async def execute():
                if snapshot.assets:
                    context.frozen_assets = tuple(
                        await self.io(
                            self.service.storage.freeze_inputs, task_id, snapshot.assets
                        )
                    )
                context.ensure_open()
                return await self.service.handlers[kind].execute(context)

            worker = asyncio.create_task(execute())
            self._live[task_id] = worker
            done, _ = await asyncio.wait({worker}, timeout=context.remaining())
            if not done:
                context.stop_requested = True
                worker.cancel()
                await asyncio.wait(
                    {worker}, timeout=self.service.policy.cancel_grace_seconds
                )
                raise AppError(
                    409, "DEADLINE_EXCEEDED", "任务已超过固定总截止，未自动重试"
                )
            outcome = worker.result()
            context.ensure_open()
            await self.io(self.service.finish, task_id, outcome=outcome)
            finalized = True
        except BaseException as error:
            if context:
                context.stop_requested = True
            if worker and not worker.done():
                # 不重复取消正在清理子进程的协程，否则会打断其停止证明与落库。
                if not worker.cancelling():
                    worker.cancel()
                await asyncio.wait(
                    {worker}, timeout=self.service.policy.cancel_grace_seconds
                )
            code, message = "TASK_FAILED", "任务未完成，请检查输入或本机服务后重新提交"
            uncertain = bool(worker and not worker.done())
            if isinstance(error, AppError | BackendError):
                code, message = error.code, error.message
                uncertain = uncertain or (
                    isinstance(error, BackendError) and error.execution_uncertain
                )
            if isinstance(error, asyncio.CancelledError):
                code, message = "APP_INTERRUPTED", "应用已停止，任务未自动重试"
            parameters = dict(
                error_code=code,
                error_message=message,
                uncertain=uncertain,
                keep_lease=bool(worker and not worker.done()),
            )
            finalized = await self.io(
                self.persist_failure, task_id, parameters, pending_only=not owned
            )
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self._runs.discard(current)

            def worker_stopped(task):
                self._live.pop(task_id, None)
                if not task.cancelled():
                    task.exception()
                if finalized and not self.service.gate.poisoned:
                    with self.service.gate.lock:
                        self.service.gate.running.discard(task_id)

            if worker:
                if worker.done():
                    worker_stopped(worker)
                else:
                    worker.add_done_callback(worker_stopped)

    def persist_failure(self, task_id, parameters, pending_only=False):
        try:
            if pending_only:
                self.service.fail_pending(
                    task_id, parameters["error_code"], parameters["error_message"]
                )
            else:
                self.service.finish(task_id, **parameters)
            # Outcome路径没有可证明的所有权，不能即时移动。未登记文件由下次
            # 独占启动的巡检按完整Asset主文件/缩略图登记集合安全隔离。
            with self.service.gate.lock:
                self._pending_finalizations.pop(task_id, None)
            return True
        except Exception:
            with self.service.gate.lock:
                self.service.gate.poisoned = True
                self._pending_finalizations[task_id] = (parameters, pending_only)
            logger.error("任务终态尚未写入：%s", task_id)
            return False

    def retry_finalizations(self):
        with self.service.gate.lock:
            pending = list(self._pending_finalizations.items())
        for task_id, (parameters, pending_only) in pending:
            if self.persist_failure(task_id, parameters, pending_only):
                with self.service.gate.lock:
                    if task_id not in self._live:
                        self.service.gate.running.discard(task_id)
                    self.service.gate.poisoned = bool(self._pending_finalizations)

    async def shutdown(self):
        def stop_admission():
            with self.service.gate.lock:
                self.service.closing = True

        await self.io(stop_admission)
        for task in set(self._runs) | set(self._live.values()):
            if (
                task is not asyncio.current_task()
                and not task.done()
                and not task.cancelling()
            ):
                task.cancel()
        # 必须证明处理协程和在途IO均已退出，才能由lifespan交出OS锁。
        # 不配合取消时保持工作区占用；强制退出进程由OS释放锁并启动恢复。
        while True:
            pending = {
                t
                for t in set(self._runs)
                | set(self._live.values())
                | set(self._io_tasks)
                if t is not asyncio.current_task() and not t.done()
            }
            if not pending:
                return
            await asyncio.gather(
                *(asyncio.shield(t) for t in pending), return_exceptions=True
            )
