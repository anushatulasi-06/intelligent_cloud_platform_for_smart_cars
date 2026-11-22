"""Who is calling and what they may do (C1, FR-9).

Every endpoint except GET /health and POST /login needs `Authorization: Bearer <token>`.
A missing or expired token is 401; a valid token with the wrong role is 403.

    from app.auth import CurrentUser, require_role

    router = APIRouter(dependencies=[Depends(require_role("cloud_staff"))])

    @router.get("/alerts")
    def list_alerts(user: CurrentUser = Depends(require_role("owner", "cloud_staff"))):
        ...     # owners: filter by user.id's cars in the query (BR-1.3)
"""
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import postgres
from app.db.repositories import CarRepo
from app.services.auth_service import decode_token

ROLES = ("owner", "iot_staff", "cloud_staff")

# auto_error=False so a missing header gives our own 401 body instead of FastAPI's.
bearer_scheme = HTTPBearer(auto_error=False, description="Paste the access_token from POST /login")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    role: str


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated",
                         headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        claims = decode_token(credentials.credentials)
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise _unauthorized() from None
    role = claims.get("role")
    if role not in ROLES or user_id < 1:
        raise _unauthorized()
    return CurrentUser(id=user_id, role=role)


def require_role(*roles: str) -> Callable[..., CurrentUser]:
    """Dependency factory: 401 if not signed in, 403 if the role is not in `roles`."""
    unknown = set(roles) - set(ROLES)
    if not roles or unknown:
        raise ValueError(f"require_role needs roles from {ROLES}, got {roles!r}")

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not allowed")
        return user

    return dependency


def owns_car(user: CurrentUser, car_id: int) -> bool:
    """Owner scope check for endpoints that take a car id. Staff roles are not limited to one owner."""
    if user.role != "owner":
        return True
    with postgres.session_scope() as session:
        return CarRepo(session).is_owned_by(car_id, user.id)
