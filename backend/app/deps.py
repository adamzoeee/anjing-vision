from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def _get_user_for_token(token: str | None, db: Session) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    try:
        payload = decode_token(token)
        user = db.get(User, int(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证无效") from exc
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    if payload["org"] != user.org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "凭证组织与用户不一致")
    return user


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    return _get_user_for_token(cred.credentials if cred else None, db)


def get_current_user_for_browser_asset(
    token: str | None = Query(default=None),
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate a browser-opened asset via header or an explicit query token."""
    return _get_user_for_token(cred.credentials if cred else token, db)


def get_org_scope(user: User = Depends(get_current_user)) -> int:
    return user.org_id


def get_org_scope_for_browser_asset(
    user: User = Depends(get_current_user_for_browser_asset),
) -> int:
    return user.org_id
