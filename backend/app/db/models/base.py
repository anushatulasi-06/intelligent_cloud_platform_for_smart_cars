"""Declarative base for every PostgreSQL table.

Constraint names follow one convention (ck_<table>_<name>, uq_<table>_<cols>, fk_..., ix_...)
so Alembic migrations are predictable and constraints can be dropped by name.
"""
from sqlalchemy import CheckConstraint, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def check_in(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    """CHECK (column IN ('a', 'b', ...)). Named ck_<table>_<name or column>."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name or column)
