"""Request and response bodies for /admin/alert-types and /admin/models (C5 Config, cloud_staff)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.constants import THRESHOLD_MAX, THRESHOLD_MIN

Severity = Literal["low", "medium", "high", "critical"]


# ---- alert types ------------------------------------------------------------------------

class AlertTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_type: str
    default_severity: Severity | None
    description: str | None
    enabled: bool | None


class AlertTypeUpdate(BaseModel):
    """PUT /admin/alert-types/{alert_type}. Every field optional; send only what changes."""
    model_config = ConfigDict(extra="forbid")

    default_severity: Severity | None = None
    description: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self):
        nulls = [f for f in self.model_fields_set if getattr(self, f) is None]
        if nulls:
            raise ValueError(f"{', '.join(sorted(nulls))} cannot be null; leave the field out instead")
        return self

    def changes(self) -> dict:
        return self.model_dump(exclude_unset=True)


# ---- models -----------------------------------------------------------------------------

class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_label: str | None
    trained_date: date | None
    accuracy: float | None
    confidence_threshold: float | None
    is_active: bool | None


class ModelThresholdUpdate(BaseModel):
    """PUT /admin/models/{id}. BR-CF-2: 0.50 to 0.95, stored with 2 decimals."""
    model_config = ConfigDict(extra="forbid")

    confidence_threshold: float = Field(ge=THRESHOLD_MIN, le=THRESHOLD_MAX)


class ModelActivated(BaseModel):
    id: int
    version_label: str | None
    is_active: Literal[True] = True


class ErrorOut(BaseModel):
    detail: str
