"""Login and the signed-in user (C1, F1). POST /login and GET /health are the only endpoints
that work without a token."""
from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.schemas.admin_config import ErrorOut
from app.schemas.auth import LoginRequest, LoginResponse, MeOut
from app.services.auth_service import AuthService

ERR = {"model": ErrorOut}

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=LoginResponse, responses={401: ERR, 422: ERR})
def login(body: LoginRequest):
    """A wrong email and a wrong password give the same message, so an attacker cannot learn
    which addresses exist (BR-1.2)."""
    return AuthService().login(body.email, body.password)


@router.get("/me", response_model=MeOut, responses={401: ERR})
def me(user: CurrentUser = Depends(get_current_user)):
    return AuthService().profile(user.id)
