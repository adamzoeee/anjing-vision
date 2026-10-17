from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import Organization, User
from ..schemas import AuthOut, LoginIn, PasswordChangeIn, RegisterIn, UserOut
from ..security import create_token, hash_password, verify_password

router = APIRouter()


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "org_name": user.org.name,
    }


def _auth_payload(user: User) -> AuthOut:
    """登录 / 注册返回体：JWT + 账号画像（含机构名与角色）。"""
    return AuthOut(token=create_token(user.id, user.org_id, user.password_hash), user=_user_payload(user))


@router.post("/register", response_model=AuthOut)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    """公开注册只创建新机构及其唯一管理员。成员由管理员建号。"""
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "邮箱已注册")
    if db.query(Organization.id).filter(Organization.name == data.org_name).first():
        raise HTTPException(409, "机构名称已存在，请登录或联系机构管理员")
    try:
        org = Organization(name=data.org_name)
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            name=data.name,
            email=data.email,
            password_hash=hash_password(data.password),
            role="admin",
        )
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.query(User.id).filter(User.email == data.email).first() is not None:
            raise HTTPException(400, "邮箱已注册") from exc
        if db.query(Organization.id).filter(Organization.name == data.org_name).first():
            raise HTTPException(409, "机构名称已存在，请登录或联系机构管理员") from exc
        raise HTTPException(409, "注册冲突，请重试") from exc
    db.refresh(user)
    return _auth_payload(user)


@router.post("/login", response_model=AuthOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    """统一登录入口：只接受邮箱 + 密码，没有旁路。"""
    user = db.query(User).filter(User.email == data.email).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用，请联系机构管理员")
    return _auth_payload(user)


@router.post("/password", response_model=UserOut)
def change_password(
    data: PasswordChangeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """本人修改密码：必须提供原密码。"""
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    if data.new_password == data.current_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    user.password_hash = hash_password(data.new_password)
    db.commit()
    db.refresh(user)
    return UserOut(**_user_payload(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(**_user_payload(user))
