"""图片解码后规范化，视频探测保留原音轨；发布成功不等于登记成功。"""

import logging
import os
import warnings
from dataclasses import asdict
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select

from app.db import Database
from app.errors import AppError
from app.media.ffmpeg import FFmpegService
from app.models import Asset, Project, ShotReferenceAsset, ShotSelection, Task
from app.models.base import utc_now
from app.schemas.assets import AssetPatch, AssetRead
from app.services.projects import check_version, check_writable, get_record
from app.services.storage import StorageService

IMAGE_LIMIT = 16 * 1024 * 1024
VIDEO_LIMIT = 512 * 1024 * 1024
logger = logging.getLogger(__name__)


def decode_image(path: Path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=("PNG", "JPEG", "WEBP")) as check:
                check.verify()
            with Image.open(path, formats=("PNG", "JPEG", "WEBP")) as decoded:
                if decoded.width * decoded.height > 40_000_000:
                    raise ValueError("pixel limit")
                decoded.load()
                return ImageOps.exif_transpose(decoded).convert("RGBA")
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        # 解码损坏是输入错误，不能误报为磁盘故障。
        raise AppError(
            422, "INVALID_MEDIA", "图片损坏或格式不受支持，请重新导入"
        ) from exc


def get_asset(session, identifier: int, project_id: int | None = None) -> Asset:
    asset = session.get(Asset, identifier)
    if (
        asset is None
        or asset.deleted_at
        or (project_id is not None and asset.project_id != project_id)
    ):
        raise AppError(404, "ASSET_NOT_FOUND", "素材不存在或不属于当前项目")
    return asset


def snapshot_uses_asset(value, identifier: int, context: str = "") -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key.endswith("asset_id")
                or key == "id"
                and context in ("assets", "references", "first_frame")
            ) and item == identifier:
                return True
            if (
                key.endswith("asset_ids")
                and isinstance(item, list)
                and identifier in item
            ):
                return True
            if snapshot_uses_asset(item, identifier, key):
                return True
    if isinstance(value, list):
        return any(snapshot_uses_asset(item, identifier, context) for item in value)
    return False


class AssetService:
    def __init__(self, database: Database, storage: StorageService):
        self.database, self.storage = database, storage

    def list_assets(self, project_id: int, kind=None, tag=None, shot_id=None):
        with self.database.session_scope() as session:
            get_record(session, Project, project_id)
            query = select(Asset).where(
                Asset.project_id == project_id, Asset.deleted_at.is_(None)
            )
            if kind:
                query = query.where(Asset.type == kind)
            if shot_id is not None:
                query = query.where(Asset.shot_id == shot_id)
            assets = session.scalars(query.order_by(Asset.id.desc()))
            return [
                AssetRead.model_validate(a) for a in assets if not tag or tag in a.tags
            ]

    def register(self, project_id: int, values: dict) -> AssetRead:
        with self.database.session_scope(write=True) as session:
            check_writable(get_record(session, Project, project_id))
            record = Asset(project_id=project_id, **values)
            session.add(record)
            session.flush()
            return AssetRead.model_validate(record)

    def upload(self, project_id: int, upload: UploadFile) -> AssetRead:
        with self.database.session_scope() as session:
            check_writable(get_record(session, Project, project_id))
        raw, image, thumb = (
            self.storage.new_temp(ext) for ext in (".bin", ".png", ".png")
        )
        published = []
        registered = False
        try:
            with raw.open("xb") as target:
                size = 0
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > VIDEO_LIMIT:
                        raise AppError(413, "UPLOAD_TOO_LARGE", "视频不能超过512 MiB")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            with raw.open("rb") as source:
                header = source.read(12)
            is_video = len(header) >= 12 and header[4:8] == b"ftyp"
            name = (
                (upload.filename or "未命名素材")
                .replace("\\", "/")
                .split("/")[-1][:200]
            )
            if is_video:
                info = FFmpegService().probe(raw)
                FFmpegService().thumbnail(raw, thumb)
                source = raw.with_suffix(".mp4")
                raw.rename(source)
                raw = source
                values = dict(type="video", mime_type="video/mp4", **asdict(info))
            else:
                if size > IMAGE_LIMIT:
                    raise AppError(413, "UPLOAD_TOO_LARGE", "图片不能超过16 MiB")
                with decode_image(raw) as normalized:
                    normalized.save(image, "PNG")
                    width, height = normalized.size
                    normalized.thumbnail((320, 320))
                    normalized.save(thumb, "PNG")
                source = image
                values = dict(
                    type="image",
                    mime_type="image/png",
                    width=width,
                    height=height,
                    duration_seconds=None,
                    has_audio=False,
                    audio_sample_rate=None,
                    audio_channels=None,
                )
            main = self.storage.publish_file(source, project_id)
            published.append(main.path)
            thumbnail = self.storage.publish_file(thumb, project_id)
            published.append(thumbnail.path)
            result = self.register(
                project_id,
                dict(
                    **values,
                    name=name or "未命名素材",
                    file_path=str(main.path),
                    thumbnail_path=str(thumbnail.path),
                    sha256=main.sha256,
                    size_bytes=main.size_bytes,
                    tags=[],
                    source_task_id=None,
                    shot_id=None,
                    metadata_json={
                        "thumbnail_sha256": thumbnail.sha256,
                        "origin": "upload",
                    },
                ),
            )
            registered = True
            return result
        except OSError as exc:
            raise AppError(
                503, "FILE_WRITE_FAILED", "素材读写失败，请检查文件后重试"
            ) from exc
        finally:
            # 若登记失败或图片解码异常，暂存文件不能出现在素材列表。
            if not registered:
                self.storage.isolate(published)
            for path in (raw, image, thumb):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.error("暂存文件清理失败，未影响已登记素材")

    def update(self, asset_id: int, data: AssetPatch) -> AssetRead:
        with self.database.session_scope(write=True) as session:
            asset = get_asset(session, asset_id)
            check_writable(get_record(session, Project, asset.project_id))
            check_version(asset, data.expected_version)
            for key, value in data.model_dump(
                exclude_unset=True, exclude={"expected_version"}
            ).items():
                setattr(asset, key, value)
            asset.version += 1
            session.flush()
            return AssetRead.model_validate(asset)

    def delete(self, asset_id: int, version: int) -> AssetRead:
        with self.database.session_scope(write=True) as session:
            asset = get_asset(session, asset_id)
            check_writable(get_record(session, Project, asset.project_id))
            check_version(asset, version)
            reference = session.scalar(
                select(ShotReferenceAsset.shot_id)
                .where(ShotReferenceAsset.asset_id == asset_id)
                .limit(1)
            )
            selection = session.scalar(
                select(ShotSelection.shot_id)
                .where(
                    (ShotSelection.first_frame_asset_id == asset_id)
                    | (ShotSelection.selected_video_asset_id == asset_id)
                )
                .limit(1)
            )
            history = session.scalars(
                select(Task).where(Task.project_id == asset.project_id)
            )
            if (
                reference is not None
                or selection is not None
                or asset.source_task_id
                or any(
                    t.output_asset_id == asset_id
                    or snapshot_uses_asset(t.input_snapshot, asset_id)
                    for t in history
                )
            ):
                raise AppError(
                    409, "ASSET_IN_USE", "素材被分镜或历史任务引用，不能删除"
                )
            asset.deleted_at = utc_now()
            asset.version += 1
            session.flush()
            return AssetRead.model_validate(asset)

    def content(self, asset_id: int, project_id: int, thumbnail: bool = False):
        with self.database.session_scope() as session:
            asset = get_asset(session, asset_id, project_id)
            self.storage.checked_path(Path(asset.file_path), self.storage.media)
            self.storage.checked_file(Path(asset.file_path), asset.sha256)
            path = asset.thumbnail_path if thumbnail else asset.file_path
            if not path:
                raise AppError(404, "THUMBNAIL_MISSING", "此素材没有缩略图")
            self.storage.checked_path(Path(path), self.storage.media)
            digest = (
                asset.metadata_json.get("thumbnail_sha256")
                if thumbnail
                else asset.sha256
            )
            self.storage.checked_file(Path(path), digest)
            return Path(path), "image/png" if thumbnail else asset.mime_type, asset.name
