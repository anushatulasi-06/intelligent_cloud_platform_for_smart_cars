"""Allowed values for enum columns. Stored as plain strings with a CHECK constraint."""
import enum

from sqlalchemy import Enum as SAEnum


class Role(str, enum.Enum):
    owner = "owner"
    iot_staff = "iot_staff"
    cloud_staff = "cloud_staff"


class DeviceType(str, enum.Enum):
    audio = "audio"
    camera = "camera"  # managed by IoT staff (FR-3) but never sends data for classification


class DeviceStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    disabled = "disabled"


class DevicePosition(str, enum.Enum):
    cabin = "cabin"
    front = "front"
    rear = "rear"
    left = "left"
    right = "right"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class NotifyMethod(str, enum.Enum):
    email = "email"
    in_app = "in_app"


def str_enum(enum_cls: type[enum.Enum]) -> SAEnum:
    """Store the enum's value (not its name) as VARCHAR + CHECK, portable across databases."""
    return SAEnum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
    )
