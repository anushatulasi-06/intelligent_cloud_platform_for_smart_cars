"""Request and response bodies for POST /login and GET /me (C1, F1)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["owner", "iot_staff", "cloud_staff"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _an_email_address(cls, value: str) -> str:
        value = value.strip().lower()
        local, _, domain = value.partition("@")
        if not local or not domain:
            raise ValueError("must be an email address")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: Role
    user: UserOut


class MeOut(UserOut):
    role: Role
