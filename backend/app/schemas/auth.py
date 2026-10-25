"""Auth request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


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


class EmailAvailability(BaseModel):
    """Whether the deployment can currently deliver account emails."""

    available: bool = False


class WsTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4096)
    password: str = Field(min_length=8, max_length=128)


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class MessageResponse(BaseModel):
    message: str
