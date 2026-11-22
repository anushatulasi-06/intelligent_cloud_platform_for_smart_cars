"""Compatibility shim. The role headers are gone; real JWT auth now lives in app/auth.py (C1).

Routers written before the swap still do `from app.auth_stub import require_role`, and those
imports keep working through this module. New code should import from app.auth instead.
"""
from app.auth import ROLES, CurrentUser, get_current_user, require_role

__all__ = ["ROLES", "CurrentUser", "get_current_user", "require_role"]
