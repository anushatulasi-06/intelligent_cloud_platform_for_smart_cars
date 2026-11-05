"""alert_type_config (PostgreSQL)."""
from sqlalchemy import select

from app.db.models import AlertTypeConfig
from app.db.repositories.base import SqlRepo


class AlertTypeRepo(SqlRepo[AlertTypeConfig]):
    model = AlertTypeConfig
    time_column = None

    def list_all(self) -> list[AlertTypeConfig]:
        return list(self.session.scalars(select(AlertTypeConfig).order_by(AlertTypeConfig.alert_type)))
