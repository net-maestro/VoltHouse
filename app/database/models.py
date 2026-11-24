import enum
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class OutageEventType(str, enum.Enum):
    outage = "outage"
    emergency = "emergency"
    normal = "normal"


class House(Base):
    __tablename__ = "houses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dtek_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="Черга 5.1")
    battery_capacity_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    users: Mapped[List["User"]] = relationship("User", back_populates="house", lazy="selectin")
    outage_logs: Mapped[List["OutageLog"]] = relationship(
        "OutageLog", back_populates="house", cascade="all, delete-orphan"
    )
    telemetry_logs: Mapped[List["TelemetryLog"]] = relationship(
        "TelemetryLog", back_populates="house", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    house_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("houses.id", ondelete="SET NULL"), nullable=True
    )
    apartment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Personal notification preferences (/settings)
    notify_outages: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_voltage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_critical_soc: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    house: Mapped[Optional["House"]] = relationship("House", back_populates="users", lazy="selectin")


class OutageLog(Base):
    __tablename__ = "outage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    house_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("houses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_soc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    end_soc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    event_type: Mapped[OutageEventType] = mapped_column(
        Enum(OutageEventType, name="outage_event_type"),
        nullable=False,
        default=OutageEventType.outage,
    )

    house: Mapped["House"] = relationship("House", back_populates="outage_logs")


class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    house_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("houses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    soc: Mapped[float] = mapped_column(Float, nullable=False)
    load_power_kw: Mapped[float] = mapped_column(Float, nullable=False)
    battery_power_kw: Mapped[float] = mapped_column(Float, nullable=False)
    grid_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    v_l1: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    v_l2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    v_l3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    house: Mapped["House"] = relationship("House", back_populates="telemetry_logs")
