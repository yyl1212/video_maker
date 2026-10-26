from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from .common import InputModel, RecordRead, Version

EpisodeTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class EpisodeCreate(InputModel):
    title: EpisodeTitle
    script_text: str = Field(default="", max_length=12000)


class EpisodePatch(InputModel):
    expected_version: Version
    title: EpisodeTitle | None = None
    script_text: str | None = Field(default=None, max_length=12000)
    archived: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def no_null_changes(self):
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set - {"expected_version"}
        ):
            raise ValueError("修改字段不能为null")
        return self


class EpisodeRead(RecordRead):
    project_id: int
    title: str
    script_text: str
    archived_at: datetime | None
