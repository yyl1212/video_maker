"""BackgroundTasks只执行已登记任务；数据库调用放入短线程事务。"""

import asyncio
import logging

from app.errors import AppError, BackendError
from app.services.tasks import InputAsset, TaskSnapshot

logger = logging.getLogger(__name__)


class TaskContext:
    def __init__(self, service, task_id, snapshot):
        self.service, self.task_id, self.snapshot = service, task_id, snapshot
        self.frozen_assets = ()
        self.job_dir = service.storage.checked_path(
            service.storage.root / "jobs" / str(task_id)
        )
        self.job_dir.mkdir(parents=True, exist_ok=True)

    async def report(self, stage: str, progress: float | None = None):
        await asyncio.to_thread(
            self.service.running_update, self.task_id, stage=stage, progress=progress
        )

    async def mark_remote_started(self, backend: str):
        if backend not in ("llm", "drawthings", "mlx"):
            raise AppError(422, "UNKNOWN_BACKEND", "本机模型类型不合法")
        await asyncio.to_thread(
            self.service.running_update,
            self.task_id,
            execution={
                "remote": {"backend": backend, "started": True, "finished": False},
            },
        )

    async def mark_remote_finished(self):
        def load():
            from app.models import Task

            with self.service.database.session_scope() as session:
                task = session.get(Task, self.task_id)
                return dict(task.execution_json.get("remote", {}))

        remote = await asyncio.to_thread(load)
        if not remote.get("started"):
            raise AppError(409, "REMOTE_NOT_STARTED", "没有已开始的远程执行记录")
        await asyncio.to_thread(
            self.service.running_update,
            self.task_id,
            execution={
                "remote": {**remote, "finished": True},
            },
        )


class TaskRunner:
    def __init__(self, service):
        self.service = service

    async def heartbeat(self, task_id):
        while True:
            await asyncio.sleep(self.service.policy.heartbeat_seconds)
            try:
                await asyncio.to_thread(self.service.running_update, task_id)
            except AppError:
                logger.error("任务心跳写入失败：%s", task_id)

    async def run(self, task_id: int) -> None:
        claimed = await asyncio.to_thread(self.service.claim, task_id)
        if claimed is None:
            return
        kind, input_hash, stored = claimed
        heartbeat = asyncio.create_task(self.heartbeat(task_id))
        try:
            snapshot = TaskSnapshot(
                data=stored["data"],
                input_hash=input_hash,
                assets=tuple(InputAsset(**item) for item in stored["assets"]),
                compiler_version=stored["compiler_version"],
            )
            context = TaskContext(self.service, task_id, snapshot)
            if snapshot.assets:
                context.frozen_assets = tuple(
                    await asyncio.to_thread(
                        self.service.storage.freeze_inputs, task_id, snapshot.assets
                    )
                )
            handler = self.service.handlers[kind]
            outcome = await handler.execute(context)
            await asyncio.to_thread(self.service.finish, task_id, outcome=outcome)
        except BaseException as error:
            code, message = "TASK_FAILED", "任务未完成，请检查输入或本机服务后重新提交"
            uncertain = False
            if isinstance(error, AppError | BackendError):
                code, message = error.code, error.message
                uncertain = (
                    isinstance(error, BackendError) and error.execution_uncertain
                )
            if isinstance(error, asyncio.CancelledError):
                code, message = "APP_INTERRUPTED", "应用已停止，任务未自动重试"
            try:
                await asyncio.to_thread(
                    self.service.finish,
                    task_id,
                    error_code=code,
                    error_message=message,
                    uncertain=uncertain,
                )
            except Exception:
                self.service.gate.poisoned = True
                logger.error("任务终态尚未写入：%s", task_id)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
