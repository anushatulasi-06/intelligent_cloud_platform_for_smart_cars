"""Real login tokens for tests, so they exercise the same auth the browser uses.

    from tests.auth_headers import bearer
    client.get("/alerts", headers=bearer("owner", 1))
"""
from app.services.auth_service import issue_token


def bearer(role: str, user_id: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user_id, role)}"}
