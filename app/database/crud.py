from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import House, OutageEventType, OutageLog, TelemetryLog, User


# ==========================================
# User CRUD
# ==========================================
async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
) -> User:
    stmt = (
        select(User)
        .options(selectinload(User.house))
        .where(User.telegram_id == telegram_id)
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await session.commit()
    return user


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    stmt = (
        select(User)
        .options(selectinload(User.house))
        .where(User.telegram_id == telegram_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def request_house_join(
    session: AsyncSession,
    telegram_id: int,
    house_id: int,
    apartment: str,
) -> Optional[User]:
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        return None
    user.house_id = house_id
    user.apartment = apartment.strip()
    user.is_approved = False
    await session.commit()
    await session.refresh(user)
    return user


async def approve_user(session: AsyncSession, user_id: int, approved: bool = True) -> Optional[User]:
    stmt = select(User).options(selectinload(User.house)).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        return None
    if approved:
        user.is_approved = True
    else:
        user.house_id = None
        user.apartment = None
        user.is_approved = False
    await session.commit()
    await session.refresh(user)
    return user


async def leave_house(session: AsyncSession, telegram_id: int) -> Optional[User]:
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        return None
    user.house_id = None
    user.apartment = None
    user.is_approved = False
    await session.commit()
    return user


async def toggle_user_notification(
    session: AsyncSession, telegram_id: int, field: str
) -> Optional[User]:
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user or field not in ("notify_outages", "notify_voltage", "notify_critical_soc"):
        return None
    current_val = getattr(user, field)
    setattr(user, field, not current_val)
    await session.commit()
    await session.refresh(user)
    return user


async def get_approved_users_for_house(session: AsyncSession, house_id: int) -> List[User]:
    stmt = select(User).where(User.house_id == house_id, User.is_approved.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_pending_users(session: AsyncSession) -> List[User]:
    stmt = (
        select(User)
        .options(selectinload(User.house))
        .where(User.house_id.is_not(None), User.is_approved.is_(False))
        .order_by(User.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ==========================================
# House CRUD
# ==========================================
async def get_all_houses(session: AsyncSession) -> List[House]:
    stmt = select(House).order_by(House.name.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_house_by_id(session: AsyncSession, house_id: int) -> Optional[House]:
    stmt = select(House).where(House.id == house_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_house(
    session: AsyncSession,
    station_id: str,
    name: str,
    dtek_group: Optional[str] = "Черга 5.1",
    battery_capacity_kwh: float = 50.0,
) -> House:
    stmt = select(House).where(House.station_id == str(station_id))
    result = await session.execute(stmt)
    house = result.scalar_one_or_none()
    if house:
        house.name = name
        if dtek_group:
            house.dtek_group = dtek_group
        house.last_sync_at = datetime.now()
    else:
        house = House(
            station_id=str(station_id),
            name=name,
            dtek_group=dtek_group or "Черга 5.1",
            battery_capacity_kwh=battery_capacity_kwh,
            last_sync_at=datetime.now(),
        )
        session.add(house)
    await session.commit()
    await session.refresh(house)
    return house


# ==========================================
# Outage Logs & Telemetry CRUD
# ==========================================
async def get_active_outage(session: AsyncSession, house_id: int) -> Optional[OutageLog]:
    stmt = (
        select(OutageLog)
        .where(OutageLog.house_id == house_id, OutageLog.end_time.is_(None))
        .order_by(OutageLog.start_time.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_last_closed_outage(session: AsyncSession, house_id: int) -> Optional[OutageLog]:
    stmt = (
        select(OutageLog)
        .where(OutageLog.house_id == house_id, OutageLog.end_time.is_not(None))
        .order_by(OutageLog.end_time.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def start_outage_log(
    session: AsyncSession,
    house_id: int,
    start_soc: float,
    event_type: OutageEventType = OutageEventType.outage,
    start_time: Optional[datetime] = None,
) -> OutageLog:
    active = await get_active_outage(session, house_id)
    if active:
        return active
    log = OutageLog(
        house_id=house_id,
        start_time=start_time or datetime.now(),
        start_soc=start_soc,
        event_type=event_type,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log


async def close_outage_log(
    session: AsyncSession,
    house_id: int,
    end_soc: float,
    end_time: Optional[datetime] = None,
) -> Optional[OutageLog]:
    active = await get_active_outage(session, house_id)
    if not active:
        return None
    active.end_time = end_time or datetime.now()
    active.end_soc = end_soc
    await session.commit()
    await session.refresh(active)
    return active


async def get_outages_for_period(
    session: AsyncSession, house_id: int, days: int = 7
) -> List[OutageLog]:
    since = datetime.now() - timedelta(days=days)
    stmt = (
        select(OutageLog)
        .where(OutageLog.house_id == house_id, OutageLog.start_time >= since)
        .order_by(OutageLog.start_time.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def record_telemetry(
    session: AsyncSession,
    house_id: int,
    soc: float,
    load_power_kw: float,
    battery_power_kw: float,
    grid_connected: bool,
    v_l1: Optional[float] = None,
    v_l2: Optional[float] = None,
    v_l3: Optional[float] = None,
    recorded_at: Optional[datetime] = None,
) -> TelemetryLog:
    entry = TelemetryLog(
        house_id=house_id,
        soc=soc,
        load_power_kw=load_power_kw,
        battery_power_kw=battery_power_kw,
        grid_connected=grid_connected,
        v_l1=v_l1,
        v_l2=v_l2,
        v_l3=v_l3,
        recorded_at=recorded_at or datetime.now(),
    )
    session.add(entry)
    await session.commit()
    return entry


async def get_telemetry_for_hours(
    session: AsyncSession, house_id: int, hours: int = 24
) -> List[TelemetryLog]:
    since = datetime.now() - timedelta(hours=hours)
    stmt = (
        select(TelemetryLog)
        .where(TelemetryLog.house_id == house_id, TelemetryLog.recorded_at >= since)
        .order_by(TelemetryLog.recorded_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
