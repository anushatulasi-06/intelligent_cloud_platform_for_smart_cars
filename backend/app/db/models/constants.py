"""Allowed values for CHECK constraints. One place, shared by models, repositories and the API."""
from app.config import SOUND_CATEGORIES

ROLES = ("owner", "iot_staff", "cloud_staff")
DEVICE_TYPES = ("microphone", "camera")
DEVICE_STATUSES = ("registered", "online", "offline", "configuring")
WIFI_SECURITY = ("open", "wpa2", "wpa3")
IP_MODES = ("dhcp", "static")
ALERT_TYPES = SOUND_CATEGORIES                      # the five sound categories
SEVERITIES = ("low", "medium", "high", "critical")
NOTIFY_METHODS = ("email", "in_app")
JOB_TYPES = ("backup", "retention")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")
THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_DEFAULT = 0.50, 0.95, 0.75
