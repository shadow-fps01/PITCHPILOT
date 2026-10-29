"""Script (pitch outline) request/response schemas."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_keywords(value: list[str]) -> list[str]:
    cleaned = [keyword.strip() for keyword in value if keyword.strip()]
    if len(cleaned) > 50:
        raise ValueError("Too many keywords (maximum 50)")
    if any(len(keyword) > 64 for keyword in cleaned):
        raise ValueError("Each keyword must be 64 characters or fewer")
    return cleaned


class ScriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content_outline: str = Field(default="", max_length=20000)
    target_wpm: int = Field(default=140, ge=60, le=240)
    keywords: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        return _clean_keywords(value)


class ScriptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content_outline: str | None = Field(default=None, max_length=20000)
    target_wpm: int | None = Field(default=None, ge=60, le=240)
    keywords: list[str] | None = Field(default=None, max_length=50)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_keywords(value)


class ScriptPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    content_outline: str
    target_wpm: int
    keywords: list[str]
