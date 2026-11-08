"""Device command queue (C2).

Revision ID: 0002_device_commands
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_device_commands"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("command", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("acked_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(200)),
        sa.CheckConstraint("command IN ('start_capture','stop_capture','reboot','sync_config',"
                           "'start_route','stop_route','simulate_event')", name="command"),
        sa.CheckConstraint("status IN ('queued','delivered','acknowledged','failed','expired')", name="status"),
    )
    op.create_index("ix_device_commands_device_id_created_at", "device_commands", ["device_id", "created_at"])
    op.create_index("uq_device_commands_queued", "device_commands", ["device_id", "command"], unique=True,
                    postgresql_where=sa.text("status = 'queued'"), sqlite_where=sa.text("status = 'queued'"))


def downgrade() -> None:
    op.drop_table("device_commands")
