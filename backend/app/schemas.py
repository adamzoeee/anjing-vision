from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


def _strip_required(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能只包含空白字符")
    return stripped


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

    _normalize_org = field_validator("org_name")(_strip_required)
    _normalize_name = field_validator("name")(_strip_required)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: Literal["admin", "member"]
    org_name: str


class AuthOut(BaseModel):
    token: str
    user: UserOut


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=200)

    _normalize_name = field_validator("name")(_strip_required)

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: str) -> str:
        return value.strip()


class ProjectOut(BaseModel):
    id: int
    name: str
    address: str


class ScanIn(BaseModel):
    capture_type: Literal["video", "photos"] = "video"


class ScanOut(BaseModel):
    id: int
    project_id: int
    status: str
    progress: int
    message: str
    capture_type: Literal["video", "photos"]
