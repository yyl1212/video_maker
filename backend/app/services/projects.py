"""项目和剧集写操作串行短事务；旧页面不能覆盖新版本。"""

from sqlalchemy import select

from app.db import Database
from app.errors import AppError
from app.models import Episode, Project, Task
from app.models.base import utc_now
from app.schemas.episodes import EpisodeCreate, EpisodePatch, EpisodeRead
from app.schemas.projects import ProjectCreate, ProjectPatch, ProjectRead


def get_record(session, model, identifier):
    record = session.get(model, identifier)
    if record is None:
        raise AppError(404, "NOT_FOUND", "项目或剧集不存在")
    return record


def check_version(record, expected):
    if record.version != expected:
        raise AppError(
            409, "VERSION_CONFLICT", "内容已被更新，请保留当前修改并重新核对最新版本"
        )


def check_writable(record):
    if record.archived_at is not None:
        raise AppError(409, "ARCHIVED", "内容已归档，请先恢复再编辑")


def apply_changes(record, data):
    fields = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if record.archived_at and any(key != "archived" for key in fields):
        check_writable(record)
    for key, value in fields.items():
        if key == "archived":
            record.archived_at = utc_now() if value else None
        else:
            setattr(record, key, value)
    if fields:
        record.version += 1


def check_no_active(session, project_id, episode_id=None):
    query = select(Task.id).where(
        Task.project_id == project_id,
        Task.status.in_(["PENDING", "RUNNING"]),
    )
    if episode_id is not None:
        query = query.where(Task.episode_id == episode_id)
    if session.scalar(query.limit(1)) is not None:
        raise AppError(409, "ACTIVE_TASK", "该内容仍有任务执行中，请完成后再归档")


class ProjectService:
    def __init__(self, database: Database):
        self.database = database

    def list_projects(self) -> list[ProjectRead]:
        with self.database.session_scope() as session:
            return [
                ProjectRead.model_validate(item)
                for item in session.scalars(
                    select(Project).order_by(Project.id.desc()),
                )
            ]

    def get_project(self, project_id: int) -> ProjectRead:
        with self.database.session_scope() as session:
            return ProjectRead.model_validate(get_record(session, Project, project_id))

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        with self.database.session_scope(write=True) as session:
            record = Project(**data.model_dump())
            session.add(record)
            session.flush()
            return ProjectRead.model_validate(record)

    def update_project(self, project_id: int, data: ProjectPatch) -> ProjectRead:
        with self.database.session_scope(write=True) as session:
            record = get_record(session, Project, project_id)
            check_version(record, data.expected_version)
            if data.archived:
                check_no_active(session, project_id)
            apply_changes(record, data)
            session.flush()
            return ProjectRead.model_validate(record)

    def list_episodes(self, project_id: int) -> list[EpisodeRead]:
        with self.database.session_scope() as session:
            get_record(session, Project, project_id)
            return [
                EpisodeRead.model_validate(item)
                for item in session.scalars(
                    select(Episode)
                    .where(Episode.project_id == project_id)
                    .order_by(Episode.id),
                )
            ]

    def get_episode(self, episode_id: int) -> EpisodeRead:
        with self.database.session_scope() as session:
            return EpisodeRead.model_validate(get_record(session, Episode, episode_id))

    def create_episode(self, project_id: int, data: EpisodeCreate) -> EpisodeRead:
        with self.database.session_scope(write=True) as session:
            check_writable(get_record(session, Project, project_id))
            record = Episode(project_id=project_id, **data.model_dump())
            session.add(record)
            session.flush()
            return EpisodeRead.model_validate(record)

    def update_episode(self, episode_id: int, data: EpisodePatch) -> EpisodeRead:
        with self.database.session_scope(write=True) as session:
            record = get_record(session, Episode, episode_id)
            check_writable(get_record(session, Project, record.project_id))
            check_version(record, data.expected_version)
            if data.archived:
                check_no_active(session, record.project_id, episode_id)
            apply_changes(record, data)
            session.flush()
            return EpisodeRead.model_validate(record)
