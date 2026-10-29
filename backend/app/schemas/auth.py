"""Auth request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.passwords import validate_password_strength


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        return validate_password_strength(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime
    plan: str = "free"
    is_plus: bool = False
    email_verified: bool = True
    consent_current: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class VerifyRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4096)


class VerificationStatus(BaseModel):
    verified: bool = False
    sent: bool = False


class WsTicketRequest(BaseModel):
    """The ticket is bound to the session it will be used for."""

    session_id: uuid.UUID


class WsTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4096)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        return validate_password_strength(value)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        return validate_password_strength(value)


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class MessageResponse(BaseModel):
    message: str
