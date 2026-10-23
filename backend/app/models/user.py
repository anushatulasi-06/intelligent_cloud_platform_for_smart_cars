from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.postgres import Base
from backend.app.models.enums import Role, str_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[Role] = mapped_column(str_enum(Role))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cars: Mapped[list["Car"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
