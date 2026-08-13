from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def _strip_required(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能只包含空白字符")
    return stripped


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr = Field(max_length=120)
    password: str = Field(min_length=8, max_length=64)

    _normalize_org = field_validator("org_name")(_strip_required)
    _normalize_name = field_validator("name")(_strip_required)

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
    capture_type: Literal["video", "photos"]
    reference_measurements: list[ReferenceMeasurement] = Field(default_factory=list)
