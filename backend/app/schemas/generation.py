"""生成请求只接受意图、版本及指纹，路径和模型输入由服务端产生。"""

from uuid import UUID

from pydantic import Field

from .clients import ImageSettings
from .common import InputModel, Version


class ImageJobSubmit(InputModel):
    idempotency_key: UUID
    expected_project_version: Version
    expected_episode_version: Version
    expected_shot_version: Version
    expected_image_input_hash: str = Field(pattern="^[0-9a-f]{64}$")


class ConnectionTest(InputModel):
    image: ImageSettings
