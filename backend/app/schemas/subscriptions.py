"""Request and response bodies for /subscriptions (C1, F3)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertType = Literal["emergency", "in_vehicle", "human", "traffic", "environmental"]
NotifyMethod = Literal["email", "in_app"]


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    car_id: int
    alert_type: str
    enabled: bool
    notify_method: str


class SubscriptionUpsert(BaseModel):
    """POST /subscriptions. One row per (owner, car, category), so this creates or updates."""
    model_config = ConfigDict(extra="forbid")

    car_id: int = Field(ge=1)
    alert_type: AlertType
    enabled: bool
    notify_method: NotifyMethod = "email"
