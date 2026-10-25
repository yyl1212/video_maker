"""迁移使用同一连接策略，历史版本不从现有模型动态建表。"""

from alembic import context

from app.config import AppSettings
from app.db import Database
from app.models import Base
from app.models.base import UTCDateTime

config = context.config


def render_type(item_type, item, autogen_context):
    if item_type == "type" and isinstance(item, UTCDateTime):
        return "sa.DateTime()"
    return False


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_item=render_type,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=f"sqlite:///{AppSettings().database_path}",
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    database = Database(AppSettings())
    try:
        with database.engine.connect() as connection:
            run(connection)
    finally:
        database.close()
