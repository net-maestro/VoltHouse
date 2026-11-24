"""Initial schema for users, houses, outage_logs, telemetry_logs

Revision ID: 0001_initial
Revises: 
Create Date: 2026-11-17 15:25:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "houses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("dtek_group", sa.String(length=50), nullable=True),
        sa.Column("battery_capacity_kwh", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_houses_station_id"), "houses", ["station_id"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("full_name", sa.String(length=150), nullable=True),
        sa.Column("house_id", sa.Integer(), nullable=True),
        sa.Column("apartment", sa.String(length=50), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("notify_outages", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("notify_voltage", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("notify_critical_soc", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["house_id"], ["houses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    op.create_table(
        "outage_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("house_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("start_soc", sa.Float(), nullable=True),
        sa.Column("end_soc", sa.Float(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum("outage", "emergency", "normal", name="outage_event_type"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["house_id"], ["houses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_outage_logs_house_id"), "outage_logs", ["house_id"], unique=False)
    op.create_index(op.f("ix_outage_logs_start_time"), "outage_logs", ["start_time"], unique=False)

    op.create_table(
        "telemetry_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("house_id", sa.Integer(), nullable=False),
        sa.Column("soc", sa.Float(), nullable=False),
        sa.Column("load_power_kw", sa.Float(), nullable=False),
        sa.Column("battery_power_kw", sa.Float(), nullable=False),
        sa.Column("grid_connected", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("v_l1", sa.Float(), nullable=True),
        sa.Column("v_l2", sa.Float(), nullable=True),
        sa.Column("v_l3", sa.Float(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["house_id"], ["houses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_telemetry_logs_house_id"), "telemetry_logs", ["house_id"], unique=False
    )
    op.create_index(
        op.f("ix_telemetry_logs_recorded_at"), "telemetry_logs", ["recorded_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_telemetry_logs_recorded_at"), table_name="telemetry_logs")
    op.drop_index(op.f("ix_telemetry_logs_house_id"), table_name="telemetry_logs")
    op.drop_table("telemetry_logs")
    op.drop_index(op.f("ix_outage_logs_start_time"), table_name="outage_logs")
    op.drop_index(op.f("ix_outage_logs_house_id"), table_name="outage_logs")
    op.drop_table("outage_logs")
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_houses_station_id"), table_name="houses")
    op.drop_table("houses")
