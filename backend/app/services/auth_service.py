"""Login, password hashing and JWT tokens (C1, FR-9).

The token carries the user id and role, so checking a request never needs a database query.
app/auth.py turns the token back into a CurrentUser.
"""
import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings
from app.db import postgres
from app.db.repositories import UserRepo
from app.services.errors import Unauthorized

log = logging.getLogger(__name__)

BCRYPT_ROUNDS = 12
INVALID_LOGIN = "Invalid email or password"      # same message either way (BR-1.2)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False                              # not a bcrypt hash: treat as a failed login


def issue_token(user_id: int, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "role": role, "iat": now,
              "exp": now + timedelta(minutes=s.JWT_TTL_MINUTES)}
    return jwt.encode(claims, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Claims of a valid token. Raises jwt.PyJWTError when the signature or the expiry is wrong."""
    s = get_settings()
    return jwt.decode(token, s.JWT_SECRET, algorithms=[s.JWT_ALGORITHM])


class AuthService:
    def login(self, email: str, password: str) -> dict:
        settings = get_settings()
        with postgres.session_scope() as session:
            user = UserRepo(session).get_by_email(email)
            if user is None or not verify_password(password, user.password_hash):
                log.warning("failed login for %s", email)
                raise Unauthorized(INVALID_LOGIN)
            return {
                "access_token": issue_token(user.id, user.role),
                "token_type": "bearer",
                "expires_in": settings.JWT_TTL_MINUTES * 60,
                "role": user.role,
                "user": {"id": user.id, "name": user.name, "email": user.email},
            }

    def profile(self, user_id: int) -> dict:
        with postgres.session_scope() as session:
            user = UserRepo(session).get(user_id)
            if user is None:                      # the account was removed after the token was issued
                raise Unauthorized("Not authenticated")
            return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}
