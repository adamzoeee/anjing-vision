from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=6, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AuthOut(BaseModel):
    token: str
    user: dict


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    org_name: str


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = ""


class ProjectOut(BaseModel):
    id: int
    name: str
    address: str


class ScanIn(BaseModel):
    capture_type: str = "video"  # video | photos


class ScanOut(BaseModel):
    id: int
    project_id: int
    status: str
    progress: int
    message: str
    capture_type: str
