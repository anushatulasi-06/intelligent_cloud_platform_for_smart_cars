"""HTTP routers, one module per area (alerts.py, config.py, ...).

Every router except health.py must protect its endpoints with require_role from app.auth_stub,
so swapping in the C1 JWT version only changes that one file.
"""
