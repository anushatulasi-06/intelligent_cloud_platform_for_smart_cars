"""Initial schema: users, cars, devices, subscriptions, alert_type_config, model_versions,
maintenance_jobs.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = sa.DateTime(timezone=True)
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(120), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("password_hash", sa.String(60), nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint(_in("role", ("owner", "iot_staff", "cloud_staff")), name="ck_users_role"),
    )

    op.create_table(
        "cars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("make", sa.String(40), nullable=False),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("plate", sa.String(12), nullable=True),
        sa.Column("last_lat", sa.Double(), nullable=True),
        sa.Column("last_lon", sa.Double(), nullable=True),
        sa.Column("last_position_at", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cars"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_cars_owner_id_users"),
        sa.UniqueConstraint("plate", name="uq_cars_plate"),
    )
    op.create_index("ix_cars_owner_id", "cars", ["owner_id"])

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("device_type", sa.String(10), nullable=True),
        sa.Column("status", sa.String(12), server_default=sa.text("'registered'"), nullable=True),
        sa.Column("wifi_ssid", sa.String(32), nullable=True),
        sa.Column("wifi_security", sa.String(5), nullable=True),
        sa.Column("wifi_password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("ip_mode", sa.String(6), nullable=True),
        sa.Column("device_key_hash", sa.CHAR(64), nullable=False),
        sa.Column("firmware", sa.String(20), nullable=True),
        sa.Column("last_seen", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], name="fk_devices_car_id_cars"),
        sa.UniqueConstraint("car_id", "name", name="uq_devices_car_id_name"),
        sa.CheckConstraint(_in("device_type", ("microphone", "camera")), name="ck_devices_device_type"),
        sa.CheckConstraint(_in("status", ("registered", "online", "offline", "configuring")),
                           name="ck_devices_status"),
        sa.CheckConstraint(_in("wifi_security", ("open", "wpa2", "wpa3")), name="ck_devices_wifi_security"),
        sa.CheckConstraint(_in("ip_mode", ("dhcp", "static")), name="ck_devices_ip_mode"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(16), nullable=False),   # 12 cannot hold "environmental"
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.Column("notify_method", sa.String(6), server_default=sa.text("'email'"), nullable=True),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_subscriptions_owner_id_users"),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE", name="fk_subscriptions_car_id_cars"),
        sa.UniqueConstraint("owner_id", "car_id", "alert_type", name="uq_subscriptions_owner_id_car_id_alert_type"),
        sa.CheckConstraint(_in("alert_type", ("emergency", "in_vehicle", "human", "traffic", "environmental")),
                           name="ck_subscriptions_alert_type"),
        sa.CheckConstraint(_in("notify_method", ("email", "in_app")), name="ck_subscriptions_notify_method"),
    )

    op.create_table(
        "alert_type_config",
        sa.Column("alert_type", sa.String(16), nullable=False),  # 12 cannot hold "environmental"
        sa.Column("default_severity", sa.String(8), nullable=True),
        sa.Column("description", sa.String(120), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.PrimaryKeyConstraint("alert_type", name="pk_alert_type_config"),
        sa.CheckConstraint(_in("default_severity", ("low", "medium", "high", "critical")),
                           name="ck_alert_type_config_default_severity"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(20), server_default=sa.text("'audio_classifier'"), nullable=True),
        sa.Column("version_label", sa.String(30), nullable=True),
        sa.Column("trained_date", sa.Date(), nullable=True),
        sa.Column("accuracy", sa.Numeric(4, 3), nullable=True),
        sa.Column("confidence_threshold", sa.Numeric(3, 2), server_default=sa.text("0.75"), nullable=True),
        sa.Column("artifact_path", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_model_versions"),
        sa.UniqueConstraint("version_label", name="uq_model_versions_version_label"),
        sa.CheckConstraint("confidence_threshold BETWEEN 0.50 AND 0.95",
                           name="ck_model_versions_confidence_threshold"),
    )
    # At most one active model: only rows with is_active = true are in this unique index.
    op.create_index("uq_model_versions_one_active", "model_versions", ["is_active"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))

    op.create_table(
        "maintenance_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(12), nullable=True),
        sa.Column("status", sa.String(10), nullable=True),
        sa.Column("params", JSONB, nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("started_at", TZ, nullable=True),
        sa.Column("finished_at", TZ, nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_maintenance_jobs"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_maintenance_jobs_created_by_users"),
        sa.CheckConstraint(_in("job_type", ("backup", "retention")), name="ck_maintenance_jobs_job_type"),
        sa.CheckConstraint(_in("status", ("queued", "running", "succeeded", "failed")),
                           name="ck_maintenance_jobs_status"),
    )


def downgrade() -> None:
    op.drop_table("maintenance_jobs")
    op.drop_index("uq_model_versions_one_active", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_table("alert_type_config")
    op.drop_table("subscriptions")
    op.drop_table("devices")
    op.drop_index("ix_cars_owner_id", table_name="cars")
    op.drop_table("cars")
    op.drop_table("users")
