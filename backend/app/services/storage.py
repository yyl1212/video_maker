"""不可变文件发布与快照；路径只来自服务端，不接受浏览器磁盘路径。"""

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.errors import AppError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedFile:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class FrozenAsset:
    asset_id: int
    path: Path
    sha256: str


class StorageService:
    def __init__(self, data_dir: Path):
        self.root = data_dir.absolute()
        self.staging = self.root / "staging"
        self.media = self.root / "media"
        self.inputs = self.root / "task-inputs"
        self.quarantine = self.root / "quarantine"
        for path in (self.staging, self.media, self.inputs, self.quarantine):
            self.checked_path(path)
            path.mkdir(parents=True, exist_ok=True)

    def checked_path(self, path: Path, parent: Path | None = None) -> Path:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise AppError(409, "UNSAFE_PATH", "素材路径不在受管目录中")
        try:
            path.relative_to(parent or self.root)
            # resolve会掩盖目录中的符号链接，必须逐级检查原路径。
            for item in (path, *path.parents):
                if item.is_symlink():
                    raise ValueError("symlink")
        except (ValueError, OSError) as exc:
            raise AppError(409, "UNSAFE_PATH", "素材路径不在受管目录中") from exc
        return path

    def checked_file(self, path: Path, sha256: str | None = None) -> Path:
        path = self.checked_path(path)
        try:
            if not path.is_file():
                raise OSError("missing")
            if sha256 and self.digest(path) != sha256:
                raise AppError(409, "ASSET_CHANGED", "素材文件已变化，请重新导入")
        except OSError as exc:
            raise AppError(
                409, "ASSET_MISSING", "素材文件不可读取，请重新导入"
            ) from exc
        return path

    @staticmethod
    def digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def new_temp(self, suffix: str) -> Path:
        return self.staging / f"{uuid4().hex}{suffix}"

    def publish_file(self, temp_path: Path, project_id: int) -> PublishedFile:
        self.checked_path(temp_path, self.staging)
        self.checked_file(temp_path)
        if project_id < 1 or temp_path.suffix not in (".png", ".mp4"):
            raise AppError(422, "INVALID_FILE", "不支持的发布文件")
        target_dir = self.checked_path(self.media / str(project_id))
        target_dir.mkdir(exist_ok=True)
        target = target_dir / f"{uuid4().hex}{temp_path.suffix}"
        try:
            digest = self.digest(temp_path)
            size = temp_path.stat().st_size
            # 同一文件系统硬链接只发布已完整落盘的文件，且绝不覆盖已有版本。
            with temp_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.link(temp_path, target, follow_symlinks=False)
            target.chmod(0o444)
            temp_path.unlink()
            return PublishedFile(target, digest, size)
        except OSError as exc:
            raise AppError(
                503, "FILE_WRITE_FAILED", "素材文件发布失败，请重试"
            ) from exc

    def isolate(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                self.checked_path(path, self.media)
                if path.exists():
                    path.rename(self.quarantine / f"{uuid4().hex}{path.suffix}")
            except (OSError, AppError):
                logger.error("文件隔离未完成；下次启动继续巡检")

    def quarantine_orphans(self, registered_paths: set[str]) -> int:
        orphans = [
            path
            for path in self.media.rglob("*")
            if path.is_file() and str(path) not in registered_paths
        ]
        self.isolate(orphans)
        if orphans:
            logger.warning("隔离未登记文件：%s；未修改任务状态", len(orphans))
        return len(orphans)

    def freeze_inputs(self, task_id: int, assets) -> list[FrozenAsset]:
        if task_id < 1:
            raise AppError(422, "INVALID_TASK", "任务编号无效")
        directory = self.checked_path(self.inputs / str(task_id))
        directory.mkdir(exist_ok=False)
        result = []
        try:
            for asset in assets:
                source = self.checked_file(Path(asset.file_path), asset.sha256)
                target = directory / f"{len(result):02d}-{asset.id}{source.suffix}"
                shutil.copyfile(source, target, follow_symlinks=False)
                self.checked_file(target, asset.sha256)
                target.chmod(0o444)
                result.append(FrozenAsset(asset.id, target, asset.sha256))
            return result
        except (OSError, AppError) as exc:
            shutil.rmtree(directory)
            if isinstance(exc, AppError):
                raise
            raise AppError(503, "INPUT_FREEZE_FAILED", "输入素材复制失败") from exc
