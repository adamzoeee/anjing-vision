from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import Organization, User
from ..schemas import AuthOut, LoginIn, RegisterIn, UserOut
from ..security import create_token, hash_password, verify_password

router = APIRouter()


@router.post("/register", response_model=AuthOut)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "邮箱已注册")
    org = db.query(Organization).filter(Organization.name == data.org_name).first()
    created_org = org is None
    has_admin = (
        False
        if created_org
        else db.query(User.id)
        .filter(User.org_id == org.id, User.role == "admin")
        .first()
        is not None
    )
    try:
        if created_org:
            org = Organization(name=data.org_name)
            db.add(org)
            db.flush()
        user = User(
            org_id=org.id,
            name=data.name,
            email=data.email,
            password_hash=hash_password(data.password),
            role="member" if has_admin else "admin",
        )
        db.add(user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if db.query(User.id).filter(User.email == data.email).first() is not None:
            raise HTTPException(400, "邮箱已注册") from exc
        org = db.query(Organization).filter(Organization.name == data.org_name).first()
        admin_exists = (
            org is not None
            and db.query(User.id)
            .filter(User.org_id == org.id, User.role == "admin")
            .first()
            is not None
        )
        if not admin_exists:
            raise HTTPException(400, "机构注册冲突，请重试") from exc
        user = User(
            org_id=org.id,
            name=data.name,
            email=data.email,
            password_hash=hash_password(data.password),
            role="member",
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as retry_exc:
            db.rollback()
            raise HTTPException(400, "邮箱或机构已存在") from retry_exc
    db.refresh(user)
    return AuthOut(token=create_token(user.id, user.org_id),
                   user={"id": user.id, "name": user.name, "email": user.email,
                         "role": user.role, "org_name": org.name})


@router.post("/login", response_model=AuthOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    return AuthOut(token=create_token(user.id, user.org_id),
                   user={"id": user.id, "name": user.name, "email": user.email,
                         "role": user.role, "org_name": user.org.name})


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, name=user.name, email=user.email, role=user.role,
                   org_name=user.org.name)
