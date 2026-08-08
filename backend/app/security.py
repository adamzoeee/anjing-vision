import datetime as dt

import bcrypt as _bcrypt
import jwt

from .config import get_settings

ALGO = "HS256"


def hash_password(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int, org_id: int) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "org": org_id,
        "iat": now,
        "exp": now + dt.timedelta(minutes=s.token_expire_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=[ALGO],
        options={"require": ["exp", "sub", "org"]},
    )
    if not isinstance(payload.get("sub"), str) or not payload["sub"].isdigit():
        raise jwt.InvalidTokenError("subject 无效")
    if not isinstance(payload.get("org"), int):
        raise jwt.InvalidTokenError("organization 无效")
    return payload
