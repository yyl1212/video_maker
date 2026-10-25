from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Version = Annotated[int, Field(strict=True, ge=1)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    created_at: datetime
    updated_at: datetime
