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
    payload = {"sub": str(user_id), "org": org_id,
               "exp": dt.datetime.utcnow() + dt.timedelta(minutes=s.token_expire_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
