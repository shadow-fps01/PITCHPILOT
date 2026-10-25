"""Script (pitch outline) request/response schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ScriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content_outline: str = Field(default="", max_length=20000)
    target_wpm: int = Field(default=140, ge=60, le=240)
    keywords: list[str] = Field(default_factory=list, max_length=50)


class ScriptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content_outline: str | None = Field(default=None, max_length=20000)
    target_wpm: int | None = Field(default=None, ge=60, le=240)
    keywords: list[str] | None = Field(default=None, max_length=50)


class ScriptPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    content_outline: str
    target_wpm: int
    keywords: list[str]
