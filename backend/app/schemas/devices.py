from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DeviceType = Literal["microphone", "camera"]
DeviceStatus = Literal["registered", "online", "offline", "configuring"]
Command = Literal["start_capture", "stop_capture", "reboot", "sync_config", "start_route", "stop_route", "simulate_event"]
CommandStatus = Literal["queued", "delivered", "acknowledged", "failed", "expired"]


class WifiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssid: str
    security: Literal["open", "wpa2", "wpa3"]
    password: str | None = Field(default=None, repr=False)
    ip_mode: Literal["dhcp", "static"] = "dhcp"

    @field_validator("ssid")
    @classmethod
    def valid_ssid(cls, value):
        if not 1 <= len(value.encode("utf-8")) <= 32:
            raise ValueError("SSID must be 1 to 32 bytes")
        return value

    @model_validator(mode="after")
    def valid_password(self):
        if self.security == "open" and self.password:
            raise ValueError("Password not allowed for open networks")
        if self.security != "open" and not 8 <= len(self.password or "") <= 63:
            raise ValueError("Password must be 8 to 63 characters")
        return self


class DeviceCreate(BaseModel):
    car_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=60)
    device_type: DeviceType
    wifi: WifiConfig | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class DeviceUpdate(BaseModel):
    car_id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    device_type: DeviceType | None = None

    @model_validator(mode="after")
    def valid_fields(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Updated fields cannot be null")
        if self.name is not None:
            self.name = DeviceCreate.valid_name(self.name)
        return self


class CommandCreate(BaseModel):
    command: Command
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_scenario(self):
        if self.command == "simulate_event":
            from app.config import SOUND_CATEGORIES
            category = self.payload.get("scenario", self.payload.get("category"))
            if category not in SOUND_CATEGORIES:
                raise ValueError("scenario must be one of the five sound categories")
            self.payload = {"scenario": category}
        return self


class Heartbeat(BaseModel):
    firmware: str | None = Field(default=None, max_length=20)
    town: str | None = Field(default=None, min_length=1, max_length=100)
    rssi: int | None = Field(default=None, ge=-127, le=0)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    speed_kmh: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    heading: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)

    @model_validator(mode="after")
    def paired_coordinates(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be provided together")
        return self


class CommandAck(BaseModel):
    ok: bool
    detail: str | None = Field(default=None, max_length=200)


class WifiOut(BaseModel):
    ssid: str | None
    security: str | None
    ip_mode: str | None


class DeviceOut(BaseModel):
    id: int
    car_id: int
    name: str
    device_type: DeviceType | None
    status: DeviceStatus | None
    wifi: WifiOut | None
    firmware: str | None
    last_seen: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class DevicePage(BaseModel):
    items: list[DeviceOut]
    page: int
    page_size: int
    total: int


class DeviceCreated(BaseModel):
    device: DeviceOut
    device_key: str


class CommandQueued(BaseModel):
    command_id: int
    status: Literal["queued"]


class CommandOut(BaseModel):
    command_id: int
    command: Command
    status: CommandStatus
    created_at: datetime
    delivered_at: datetime | None
    acked_at: datetime | None
    expires_at: datetime


class EdgeCommand(BaseModel):
    command_id: int
    command: Command
    payload: dict[str, Any]


class HeartbeatOut(BaseModel):
    server_time: datetime
    commands: list[EdgeCommand]


class AckOut(BaseModel):
    status: Literal["acknowledged", "failed"]
