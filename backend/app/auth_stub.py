"""TEMPORARY AUTH STUB – replace with the C1 JWT version.

=====================================================================================
  REPLACE THIS FILE WITH THE C1 JWT VERSION (POST /login, Authorization: Bearer <token>).
  Keep the same public names – CurrentUser, ROLES, get_current_user, require_role – and
  every router keeps working unchanged. That is why all routers must use require_role
  from this module instead of checking roles themselves.
=====================================================================================

Until then, the caller states who they are in two headers (dev and tests only, NOT secure):
    X-Role:    owner | iot_staff | cloud_staff      (required)
    X-User-Id: integer user id                      (optional, default 1)

Errors:
    401 – no X-Role header, unknown role, or a bad X-User-Id (not signed in)
    403 – signed in, but the role is not allowed for this endpoint

Usage:
    from app.auth_stub import CurrentUser, require_role

    router = APIRouter(prefix="/admin", dependencies=[Depends(require_role("cloud_staff"))])

    @router.get("/alerts")
    def list_alerts(user: CurrentUser = Depends(require_role("owner", "cloud_staff"))):
        ...   # owners: filter by user.id's cars in the query (BR-1.3)
"""
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

ROLES = ("owner", "iot_staff", "cloud_staff")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    role: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "X-Role"})


def get_current_user(
    x_role: str | None = Header(default=None, alias="X-Role"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> CurrentUser:
    """Who is calling. The C1 version decodes the JWT here instead of reading headers."""
    if not x_role:
        raise _unauthorized("Not authenticated")
    if x_role not in ROLES:
        raise _unauthorized("Not authenticated")
    if x_user_id is None or x_user_id == "":
        user_id = 1
    else:
        try:
            user_id = int(x_user_id)
        except ValueError:
            raise _unauthorized("Not authenticated") from None
        if user_id < 1:
            raise _unauthorized("Not authenticated")
    return CurrentUser(id=user_id, role=x_role)


def require_role(*roles: str) -> Callable[..., CurrentUser]:
    """Dependency factory: 401 if not signed in, 403 if the role is not in `roles`.

    Returns the current user so handlers can scope queries by user.id.
    """
    unknown = set(roles) - set(ROLES)
    if not roles or unknown:
        raise ValueError(f"require_role needs roles from {ROLES}, got {roles!r}")

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role not allowed")
        return user

    return dependency
