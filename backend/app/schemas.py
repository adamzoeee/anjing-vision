from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def _strip_required(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能只包含空白字符")
    return stripped


def _check_bcrypt_password(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
    return value


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr = Field(max_length=120)
    password: str = Field(min_length=8, max_length=64)

    _normalize_org = field_validator("org_name")(_strip_required)
    _normalize_name = field_validator("name")(_strip_required)
    _check_password = field_validator("password")(_check_bcrypt_password)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class LoginIn(BaseModel):
    email: EmailStr = Field(max_length=120)
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
    is_active: bool = True
    org_name: str


class AuthOut(BaseModel):
    token: str
    user: UserOut


class UserListItem(BaseModel):
    """机构成员列表项：管理员在自己机构内管理账号时使用。"""

    id: int
    name: str
    email: EmailStr
    role: Literal["admin", "member"]
    is_active: bool
    created_at: datetime | None = None
    disabled_at: datetime | None = None


class UserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr = Field(max_length=120)
    password: str = Field(min_length=8, max_length=64)

    _normalize_name = field_validator("name")(_strip_required)
    _check_password = field_validator("password")(_check_bcrypt_password)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return None if value is None else _strip_required(value)

    @model_validator(mode="after")
    def _require_a_change(self):
        if self.name is None and self.is_active is None:
            raise ValueError("至少需要提供 name 或 is_active")
        return self


class PasswordResetIn(BaseModel):
    """管理员为成员重置密码（不需要原密码）。"""

    password: str = Field(min_length=8, max_length=64)
    _check_password = field_validator("password")(_check_bcrypt_password)


class PasswordChangeIn(BaseModel):
    """本人修改密码：必须校验原密码。"""

    current_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=64)
    _check_password = field_validator("new_password")(_check_bcrypt_password)


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
    defer_processing: bool = False
    capture_type: Literal["video", "photos"] = "video"


class ReferenceMeasurement(BaseModel):
    object_type: Literal["door", "bed", "sofa", "table", "cabinet", "bookshelf"]
    dimension: Literal["length", "width", "height"]
    meters: float = Field(gt=0.1, le=20.0)

    @model_validator(mode="after")
    def _supported_dimension(self):
        allowed = {
            "door": {"width", "height"},
            "bed": {"length", "width", "height"},
            "sofa": {"length", "width", "height"},
            "table": {"length", "width", "height"},
            "cabinet": {"length", "width", "height"},
            "bookshelf": {"length", "width", "height"},
        }
        if self.dimension not in allowed[self.object_type]:
            raise ValueError("该参考物不支持此尺寸方向")
        return self


class ReferenceMeasurementsIn(BaseModel):
    measurements: list[ReferenceMeasurement] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def _distinct_references(self):
        keys = {(item.object_type, item.dimension) for item in self.measurements}
        if len(keys) != len(self.measurements):
            raise ValueError("参考尺寸不能重复")
        return self


class ScanOut(BaseModel):
    id: int
    project_id: int
    status: str
    progress: int
    message: str
    capture_type: Literal["video", "photos", "house", "house_demo"]
    reference_measurements: list[ReferenceMeasurement] = Field(default_factory=list)
    created_at: datetime | None = None

    @field_validator("progress", mode="before")
    @classmethod
    def _coerce_progress(cls, value: object) -> object:
        # 兼容历史数据中可能残留的小数进度（如 25.4），统一取整
        if isinstance(value, float):
            return int(round(value))
        return value
