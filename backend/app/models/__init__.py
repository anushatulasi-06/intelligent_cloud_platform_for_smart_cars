"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from backend.app.models.alert_type_config import AlertTypeConfig
from backend.app.models.car import Car
from backend.app.models.device import Device
from backend.app.models.enums import DevicePosition, DeviceStatus, DeviceType, NotifyMethod, Role, Severity
from backend.app.models.model_version import ModelVersion
from backend.app.models.subscription import Subscription
from backend.app.models.user import User

__all__ = [
    "AlertTypeConfig",
    "Car",
    "Device",
    "DevicePosition",
    "DeviceStatus",
    "DeviceType",
    "ModelVersion",
    "NotifyMethod",
    "Role",
    "Severity",
    "Subscription",
    "User",
]
