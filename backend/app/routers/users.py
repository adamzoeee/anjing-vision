# 机构成员账号管理：仅机构管理员可用，且只能管理本机构成员。
# 账号不物理删除：停用即写入 disabled_at，保留历史归属（如改造方案的 created_by）。
# 管理员账号不能通过接口停用——每个机构只允许一个管理员（数据库唯一索引保证）。
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import PasswordResetIn, UserCreateIn, UserListItem, UserUpdateIn
from ..security import hash_password

router = APIRouter()


def _list_item(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "disabled_at": user.disabled_at,
    }


def _own_member(user_id: int, admin: User, db: Session) -> User:
    """取本机构成员；跨机构返回 404，不泄露账号是否存在。"""
    target = db.get(User, user_id)
    if target is None or target.org_id != admin.org_id:
        raise HTTPException(404, "成员不存在")
    return target


def _reject_admin_target(target: User, action: str) -> None:
    if target.role == "admin":
        raise HTTPException(409, f"机构管理员账号不能{action}")


@router.get("", response_model=list[UserListItem])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = db.query(User).filter(User.org_id == admin.org_id).order_by(User.id).all()
    return [_list_item(user) for user in users]


@router.post("", response_model=UserListItem, status_code=201)
def create_user(
    data: UserCreateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员建号：新账号固定为本机构成员，避免出现第二个管理员。"""
    if db.query(User.id).filter(User.email == data.email).first() is not None:
        raise HTTPException(400, "邮箱已注册")
    user = User(
        org_id=admin.org_id,
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        role="member",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(400, "邮箱已注册") from exc
    db.refresh(user)
    return _list_item(user)


@router.patch("/{user_id}", response_model=UserListItem)
def update_user(
    user_id: int,
    data: UserUpdateIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """改名，或启用 / 停用成员账号。"""
    target = _own_member(user_id, admin, db)
    if target.id == admin.id:
        raise HTTPException(400, "不能修改自己的账号，请使用个人密码修改")
    _reject_admin_target(target, "停用或修改")
    if data.name is not None:
        target.name = data.name
    if data.is_active is not None:
        target.disabled_at = None if data.is_active else dt.datetime.now(dt.UTC)
    db.commit()
    db.refresh(target)
    return _list_item(target)


@router.post("/{user_id}/password", response_model=UserListItem)
def reset_password(
    user_id: int,
    data: PasswordResetIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员重置成员密码：不需要原密码；成员已有登录态在 token 过期前仍有效。"""
    target = _own_member(user_id, admin, db)
    if target.id == admin.id:
        raise HTTPException(400, "不能重置自己的密码，请使用个人密码修改")
    _reject_admin_target(target, "重置密码")
    target.password_hash = hash_password(data.password)
    db.commit()
    db.refresh(target)
    return _list_item(target)
