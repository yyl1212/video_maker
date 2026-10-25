from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import InputModel, RecordRead, Version

ProjectName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class Character(InputModel):
    id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    description: str = Field(default="", max_length=4000)


def unique_characters(items):
    if len({item.id for item in items}) != len(items):
        raise ValueError("角色编号不能重复")
    return items


class ProjectCreate(InputModel):
    name: ProjectName
    style_anchor: str = Field(default="", max_length=4000)
    characters: list[Character] = Field(default_factory=list, max_length=100)

    _unique = field_validator("characters")(unique_characters)


class ProjectPatch(InputModel):
    expected_version: Version
    name: ProjectName | None = None
    style_anchor: str | None = Field(default=None, max_length=4000)
    characters: list[Character] | None = Field(default=None, max_length=100)
    archived: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def validate_values(self):
        for key in self.model_fields_set - {"expected_version"}:
            if getattr(self, key) is None:
                raise ValueError("修改字段不能为null")
        if self.characters is not None:
            unique_characters(self.characters)
        return self


class ProjectRead(RecordRead):
    name: str
    style_anchor: str
    characters: list[Character]
    archived_at: datetime | None
