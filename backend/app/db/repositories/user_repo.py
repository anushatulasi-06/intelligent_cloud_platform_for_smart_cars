"""users (PostgreSQL)."""
from sqlalchemy import select

from app.db.models import User
from app.db.repositories.base import SqlRepo


class UserRepo(SqlRepo[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        """Emails are stored lower-case, so the lookup is case-insensitive."""
        return self.session.scalar(select(User).where(User.email == email.strip().lower()))

    def add(self, obj: User) -> User:
        obj.email = obj.email.strip().lower()
        return super().add(obj)

    def list_by_role(self, role: str) -> list[User]:
        return list(self.session.scalars(select(User).where(User.role == role).order_by(User.id)))
