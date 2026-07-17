"""Password hashing (bcrypt) and JWT access tokens (PyJWT)."""
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    # Ephemeral key — fine for a quick local test, but tokens break on
    # restart and won't agree across multiple instances. Set explicitly
    # for anything real.
    SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "SECRET_KEY is not set — generated an ephemeral one for this process. "
        "Tokens will stop working on restart. Set SECRET_KEY in your environment "
        "for anything beyond local dev."
    )


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(subject: str, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Return the subject (username) if the token is valid, else None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
