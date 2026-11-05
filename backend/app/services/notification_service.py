"""Owner notifications for a new alert (C4, DT-C4-2). No push notifications.

Decision, in order:
  1. no subscription row for (car owner, car, sound_type)  -> skip
  2. subscription.enabled is false                         -> skip
  3. alert type disabled in alert_type_config              -> skip
  4. notify_method "in_app" -> nothing to send; the alert simply shows in the Alerts drawer
  5. notify_method "email"  -> send through AWS SES (DEV_FAKE_EMAIL=true only logs it)

notify_if_subscribed never raises: a sending or lookup failure is logged and must not fail alert
creation.
"""
import logging
from enum import Enum

import boto3

from app.config import get_settings
from app.db import postgres
from app.db.repositories import AlertTypeRepo, CarRepo, SubscriptionRepo, UserRepo

log = logging.getLogger(__name__)


class Decision(str, Enum):
    NO_SUBSCRIPTION = "skipped_no_subscription"
    SUBSCRIPTION_DISABLED = "skipped_subscription_disabled"
    TYPE_DISABLED = "skipped_type_disabled"
    UNKNOWN_CAR = "skipped_unknown_car"
    IN_APP = "in_app"
    EMAIL_SENT = "email_sent"
    EMAIL_LOGGED = "email_logged"          # DEV_FAKE_EMAIL
    EMAIL_FAILED = "email_failed"
    ERROR = "error"


class NotificationService:
    def __init__(self, ses_client=None):
        self._ses = ses_client

    def _ses_client(self):
        if self._ses is None:
            self._ses = boto3.client("ses", region_name=get_settings().AWS_REGION)
        return self._ses

    def notify_if_subscribed(self, alert: dict) -> Decision:
        try:
            return self._decide_and_send(alert)
        except Exception:  # noqa: BLE001 – never fail alert creation
            log.exception("notification failed for alert %s", alert.get("id"))
            return Decision.ERROR

    def _decide_and_send(self, alert: dict) -> Decision:
        car_id, sound_type = alert["car_id"], alert["sound_type"]
        with postgres.session_scope() as session:
            car = CarRepo(session).get(car_id)
            if car is None:
                return Decision.UNKNOWN_CAR
            sub = SubscriptionRepo(session).find(car.owner_id, car_id, sound_type)
            if sub is None:
                return Decision.NO_SUBSCRIPTION
            if not sub.enabled:
                return Decision.SUBSCRIPTION_DISABLED
            alert_type = AlertTypeRepo(session).get(sound_type)
            if alert_type is None or not alert_type.enabled:
                return Decision.TYPE_DISABLED
            if sub.notify_method != "email":
                return Decision.IN_APP
            owner = UserRepo(session).get(car.owner_id)
            to_address = owner.email
            plate = car.plate or f"car {car.id}"
        return self._send_email(to_address, alert, plate)

    def _send_email(self, to_address: str, alert: dict, car_label: str) -> Decision:
        sound = alert["sound_type"].replace("_", " ")
        severity = alert.get("severity", "")
        subject = f"[AutoSound] {severity} {sound} alert on {car_label}".strip()
        confidence = alert.get("confidence")
        lines = [
            f"A {sound} sound was detected on {car_label}.",
            f"Severity: {severity}",
            f"Confidence: {confidence:.0%}" if isinstance(confidence, (int, float)) else "",
            f"Time: {alert.get('timestamp')}",
            "Flagged for review (low confidence)." if alert.get("is_anomaly") else "",
            "Open the Alerts page in the Smart Car Platform for details.",
        ]
        body = "\n".join(line for line in lines if line)
        if get_settings().DEV_FAKE_EMAIL:
            log.info("DEV_FAKE_EMAIL: would email %s: %s", to_address, subject)
            return Decision.EMAIL_LOGGED
        try:
            self._ses_client().send_email(
                Source=get_settings().SES_FROM_EMAIL,
                Destination={"ToAddresses": [to_address]},
                Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
            )
            return Decision.EMAIL_SENT
        except Exception:  # noqa: BLE001 – logged, never raised
            log.exception("SES send failed for alert %s", alert.get("id"))
            return Decision.EMAIL_FAILED
