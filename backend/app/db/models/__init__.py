"""PostgreSQL table models. Every model is imported here so Base.metadata knows all tables
(Alembic autogenerate reads app.db.models.Base.metadata)."""
from app.db.models.alert_type_config import AlertTypeConfig
from app.db.models.base import Base
from app.db.models.car import Car
from app.db.models.device import Device
from app.db.models.maintenance_job import MaintenanceJob
from app.db.models.model_version import ModelVersion
from app.db.models.subscription import Subscription
from app.db.models.user import User

__all__ = ["Base", "User", "Car", "Device", "Subscription", "AlertTypeConfig", "ModelVersion", "MaintenanceJob"]
