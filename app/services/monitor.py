import asyncio
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot

from app.config import settings
from app.database.base import async_session_factory
from app.database.crud import (
    close_outage_log,
    get_all_houses,
    get_approved_users_for_house,
    get_last_closed_outage,
    record_telemetry,
    start_outage_log,
    upsert_house,
)
from app.database.models import House, OutageEventType
from app.services.deye_api import StationTelemetry, deye_client
from app.services.dtek_service import dtek_service
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)


def format_duration_ua(seconds: float) -> str:
    total_minutes = max(1, int(seconds // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0 and minutes > 0:
        return f"{hours} год {minutes} хв"
    if hours > 0:
        return f"{hours} год"
    return f"{minutes} хв"


class EnergyMonitorWorker:
    """
    Background worker that periodically polls Deye Cloud for all houses,
    caches telemetry in Redis, records history in MySQL, and sends smart alerts:
    1) Power Outage (scheduled vs emergency, duration with power)
    2) Power Restored (duration of outage, 3-phase voltage check + protective instruction)
    3) Voltage Stabilized (when abnormal voltage returns to 207-253 V)
    4) Critical Battery SOC (< 25% warning / <= 20% elevator cutoff)
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.sync_stations_from_cloud()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("EnergyMonitorWorker started (interval=%ss)", settings.POLL_INTERVAL_SECONDS)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("EnergyMonitorWorker stopped")

    async def sync_stations_from_cloud(self) -> int:
        """Synchronize stations from Deye Cloud Master Account into MySQL."""
        try:
            stations = await deye_client.get_stations()
            async with async_session_factory() as session:
                for st in stations:
                    await upsert_house(
                        session=session,
                        station_id=str(st["id"]),
                        name=st["name"],
                        dtek_group=st.get("dtek_group", "Черга 5.1"),
                    )
            logger.info("Synchronized %d stations from Deye Cloud", len(stations))
            return len(stations)
        except Exception as exc:
            logger.error("Failed to sync stations from Deye Cloud: %s", exc)
            return 0

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.poll_all_houses()
            except Exception as exc:
                logger.exception("Error in monitoring loop: %s", exc)
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

    async def poll_all_houses(self) -> None:
        async with async_session_factory() as session:
            houses = await get_all_houses(session)

        for house in houses:
            try:
                telemetry = await deye_client.get_station_telemetry(
                    station_id=house.station_id, station_name=house.name
                )
                await redis_cache.set_house_status(
                    house.station_id,
                    telemetry.to_dict(),
                    ttl=max(600, settings.POLL_INTERVAL_SECONDS * 2),
                )
                await self._process_house_telemetry(house, telemetry)
            except Exception as exc:
                logger.error("Failed polling house %s (%s): %s", house.name, house.station_id, exc)

    async def _process_house_telemetry(
        self, house: House, telemetry: StationTelemetry
    ) -> None:
        now = datetime.now()
        async with async_session_factory() as session:
            await record_telemetry(
                session=session,
                house_id=house.id,
                soc=telemetry.soc,
                load_power_kw=telemetry.load_power_kw,
                battery_power_kw=telemetry.battery_power_kw,
                grid_connected=telemetry.grid_connected,
                v_l1=telemetry.v_l1,
                v_l2=telemetry.v_l2,
                v_l3=telemetry.v_l3,
                recorded_at=now,
            )

            prev_state = await redis_cache.get_monitor_state(house.id)
            if not prev_state:
                # Initialize baseline state on first run
                await redis_cache.set_monitor_state(
                    house.id,
                    {
                        "grid_connected": telemetry.grid_connected,
                        "voltage_abnormal": not telemetry.is_voltage_normal,
                        "last_transition_iso": now.isoformat(),
                        "critical_soc_alerted": telemetry.soc <= settings.ELEVATOR_CRITICAL_SOC,
                    },
                )
                return

            prev_grid: bool = bool(prev_state.get("grid_connected", True))
            prev_voltage_abnormal: bool = bool(prev_state.get("voltage_abnormal", False))
            prev_critical_alerted: bool = bool(prev_state.get("critical_soc_alerted", False))
            last_transition_iso: str = prev_state.get("last_transition_iso", now.isoformat())
            try:
                last_transition_dt = datetime.fromisoformat(last_transition_iso)
            except ValueError:
                last_transition_dt = now

            new_transition_dt = last_transition_dt
            new_voltage_abnormal = prev_voltage_abnormal
            new_critical_alerted = prev_critical_alerted

            # 1. Check Grid Transition: ON -> OFF (Power Outage)
            if prev_grid and not telemetry.grid_connected:
                new_transition_dt = now
                is_scheduled = dtek_service.is_currently_scheduled_outage(
                    house.dtek_group or "Черга 5.1", now
                )
                event_type = (
                    OutageEventType.outage if is_scheduled else OutageEventType.emergency
                )
                await start_outage_log(
                    session=session,
                    house_id=house.id,
                    start_soc=telemetry.soc,
                    event_type=event_type,
                    start_time=now,
                )

                last_closed = await get_last_closed_outage(session, house.id)
                if last_closed and last_closed.end_time:
                    uptime_sec = (now - last_closed.end_time).total_seconds()
                else:
                    uptime_sec = (now - last_transition_dt).total_seconds()
                uptime_str = format_duration_ua(uptime_sec)

                schedule_note = (
                    f"📅 Планове відключення за графіком ДТЕК ({house.dtek_group})."
                    if is_scheduled
                    else f"⚠️ Позапланове / аварійне відключення (поза графіком {house.dtek_group})."
                )
                hrs_left = telemetry.estimate_hours_to_critical(house.battery_capacity_kwh)
                eta_line = (
                    f"⏱ До порогу відключення ліфта ({int(settings.ELEVATOR_CRITICAL_SOC)}%): "
                    f"~<b>{ format_duration_ua(hrs_left * 3600) }</b>"
                    if hrs_left is not None
                    else ""
                )

                msg = (
                    f"🔴 <b>Зникло світло — {house.name}</b>\n"
                    f"⚡️ Перехід на живлення від акумуляторів.\n\n"
                    f"🕒 Світло було: <b>{uptime_str}</b>\n"
                    f"{schedule_note}\n"
                    f"🔋 Поточний заряд АКБ: <b>{telemetry.soc:.0f}%</b>\n"
                    f"🏠 Споживання будинку: <b>{telemetry.load_power_kw:.1f} кВт</b>\n"
                    f"{eta_line}"
                ).strip()

                await self._broadcast_to_house(
                    session, house.id, msg, filter_field="notify_outages"
                )

            # 2. Check Grid Transition: OFF -> ON (Power Restored)
            elif not prev_grid and telemetry.grid_connected:
                new_transition_dt = now
                closed_log = await close_outage_log(
                    session=session,
                    house_id=house.id,
                    end_soc=telemetry.soc,
                    end_time=now,
                )
                if closed_log:
                    downtime_sec = (now - closed_log.start_time).total_seconds()
                    soc_diff = (
                        f" (заряд {closed_log.start_soc:.0f}% → {telemetry.soc:.0f}%)"
                        if closed_log.start_soc is not None
                        else ""
                    )
                else:
                    downtime_sec = (now - last_transition_dt).total_seconds()
                    soc_diff = f" (заряд {telemetry.soc:.0f}%)"

                downtime_str = format_duration_ua(downtime_sec)
                v_str = f"{telemetry.v_l1:.0f} / {telemetry.v_l2:.0f} / {telemetry.v_l3:.0f} В"

                if not telemetry.is_voltage_normal:
                    new_voltage_abnormal = True
                    voltage_block = (
                        f"⚠️ <b>Увага! Напруга поза нормою:</b> <code>{v_str}</code> "
                        f"(норма {int(settings.VOLTAGE_MIN)}–{int(settings.VOLTAGE_MAX)} В)\n\n"
                        f"🛡 <i>«Щоб не вибило автомат в ТП - НЕ ВМИКАЄМО потужні електроприлади, "
                        f"чекаємо 5-10 хвилин»</i>"
                    )
                else:
                    new_voltage_abnormal = False
                    voltage_block = f"✅ Вхідна напруга в нормі: <code>{v_str}</code>"

                msg = (
                    f"🟢 <b>Світло повернулося — {house.name}!</b>\n"
                    f"🕒 Без світла були: <b>{downtime_str}</b>{soc_diff}\n\n"
                    f"{voltage_block}"
                )
                await self._broadcast_to_house(
                    session, house.id, msg, filter_field="notify_outages"
                )

            # 3. Check Voltage Anomaly / Stabilization while Grid is ON
            elif telemetry.grid_connected:
                v_str = f"{telemetry.v_l1:.0f} / {telemetry.v_l2:.0f} / {telemetry.v_l3:.0f} В"
                if not prev_voltage_abnormal and not telemetry.is_voltage_normal:
                    new_voltage_abnormal = True
                    msg = (
                        f"⚠️ <b>Просідання / стрибок напруги — {house.name}</b>\n"
                        f"Поточна напруга по фазах: <code>{v_str}</code> "
                        f"(норма {int(settings.VOLTAGE_MIN)}–{int(settings.VOLTAGE_MAX)} В)\n\n"
                        f"🛡 <i>«Щоб не вибило автомат в ТП - НЕ ВМИКАЄМО потужні електроприлади, "
                        f"чекаємо 5-10 хвилин»</i>"
                    )
                    await self._broadcast_to_house(
                        session, house.id, msg, filter_field="notify_voltage"
                    )
                elif prev_voltage_abnormal and telemetry.is_voltage_normal:
                    new_voltage_abnormal = False
                    msg = (
                        f"✅ <b>{house.name}:</b> напруга в мережі знову в нормі: "
                        f"<code>{v_str}</code>\n"
                        f"Можна безпечно вмикати електроприлади."
                    )
                    await self._broadcast_to_house(
                        session, house.id, msg, filter_field="notify_voltage"
                    )

            # 4. Critical Battery SOC Check
            if telemetry.soc <= settings.ELEVATOR_CRITICAL_SOC and not prev_critical_alerted:
                new_critical_alerted = True
                msg = (
                    f"🚨 <b>Критичний розряд АКБ — {house.name}!</b>\n"
                    f"🔋 Заряд батарей знизився до <b>{telemetry.soc:.0f}%</b> "
                    f"(поріг ліфта {int(settings.ELEVATOR_CRITICAL_SOC)}%).\n"
                    f"🛗 Ліфт вимкнено для збереження живлення насосів та аварійного освітлення."
                )
                await self._broadcast_to_house(
                    session, house.id, msg, filter_field="notify_critical_soc"
                )
            elif telemetry.soc >= settings.ELEVATOR_CRITICAL_SOC + 5.0:
                new_critical_alerted = False

            await redis_cache.set_monitor_state(
                house.id,
                {
                    "grid_connected": telemetry.grid_connected,
                    "voltage_abnormal": new_voltage_abnormal,
                    "last_transition_iso": new_transition_dt.isoformat(),
                    "critical_soc_alerted": new_critical_alerted,
                },
            )

    async def _broadcast_to_house(
        self, session, house_id: int, text: str, filter_field: str
    ) -> None:
        users = await get_approved_users_for_house(session, house_id)
        for user in users:
            if not getattr(user, filter_field, True):
                continue
            try:
                await self.bot.send_message(chat_id=user.telegram_id, text=text)
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning("Could not send alert to user %s: %s", user.telegram_id, exc)
