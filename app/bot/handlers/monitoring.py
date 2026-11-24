from datetime import datetime
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot.keyboards import build_status_refresh_keyboard
from app.config import settings
from app.database.base import async_session_factory
from app.database.crud import (
    get_or_create_user,
    get_outages_for_period,
    get_telemetry_for_hours,
)
from app.database.models import OutageEventType, User
from app.services.charts import generate_house_charts_png
from app.services.deye_api import StationTelemetry, deye_client
from app.services.dtek_service import dtek_service
from app.services.monitor import format_duration_ua
from app.services.redis_cache import redis_cache

router = Router(name="monitoring")


async def _ensure_approved_user(telegram_id: int) -> tuple[Optional[User], Optional[str]]:
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id)

    if not user.house_id or not user.house:
        return None, (
            "🏢 Ви ще не обрали свій будинок.\n"
            "Натисніть /join, щоб обрати будинок і вказати квартиру."
        )
    if not user.is_approved:
        return None, (
            f"⏳ Ваш запит на доступ до <b>{user.house.name}</b> (кв. <b>{user.apartment}</b>) "
            f"очікує підтвердження адміністратором."
        )
    return user, None


async def _get_cached_or_fresh_telemetry(house) -> StationTelemetry:
    cached = await redis_cache.get_house_status(house.station_id)
    if cached:
        return StationTelemetry.from_dict(cached)

    telemetry = await deye_client.get_station_telemetry(
        station_id=house.station_id, station_name=house.name
    )
    await redis_cache.set_house_status(house.station_id, telemetry.to_dict(), ttl=300)
    return telemetry


def _build_status_text(user: User, telemetry: StationTelemetry) -> str:
    house = user.house
    group = house.dtek_group or "Черга 5.1"
    is_scheduled_off = dtek_service.is_currently_scheduled_outage(group)

    # 1. Grid status & schedule state
    if telemetry.grid_connected:
        grid_line = "🟢 <b>Світло є</b> (живлення від зовнішньої мережі)"
        sched_line = (
            f"📅 Стан мережі: планова робота ({group})"
            if not is_scheduled_off
            else f"📅 Стан мережі: світло є попри слот графіка ({group})"
        )
    else:
        grid_line = "🔴 <b>Світла немає</b> (робота від акумуляторів)"
        sched_line = (
            f"📅 Стан мережі: планове відключення за графіком ({group})"
            if is_scheduled_off
            else f"⚠️ Стан мережі: позапланове / аварійне відключення ({group})"
        )

    # 2. 3-phase voltages
    if telemetry.grid_connected:
        v_str = f"{telemetry.v_l1:.0f} / {telemetry.v_l2:.0f} / {telemetry.v_l3:.0f} В"
        if telemetry.is_voltage_normal:
            voltage_line = f"⚡️ Напруга (L1/L2/L3): <code>{v_str}</code> (в нормі)"
        else:
            voltage_line = (
                f"⚠️ Напруга (L1/L2/L3): <code>{v_str}</code> "
                f"<b>(поза нормою {int(settings.VOLTAGE_MIN)}–{int(settings.VOLTAGE_MAX)} В!)</b>"
            )
    else:
        voltage_line = "⚡️ Напруга мережі: <code>0 / 0 / 0 В</code> (мережа відсутня)"

    # 3. Battery charge / discharge power
    if telemetry.battery_power_kw > 0.1:
        bat_flow = f"🔌 Заряджання: <b>+{telemetry.battery_power_kw:.2f} кВт</b>"
    elif telemetry.battery_power_kw < -0.1:
        bat_flow = f"🔻 Розряджання: <b>{telemetry.battery_power_kw:.2f} кВт</b>"
    else:
        bat_flow = "⏸ Режим очікування (0.0 кВт)"

    # 4. Elevator status & critical threshold
    crit_soc = settings.ELEVATOR_CRITICAL_SOC
    if telemetry.grid_connected and telemetry.is_voltage_normal:
        elevator_line = "🛗 Статус ліфта: <b>працює від мережі</b> ✅"
    elif telemetry.soc > crit_soc:
        elevator_line = "🛗 Статус ліфта: <b>працює від батарей</b> 🔋 (заряду достатньо)"
    else:
        elevator_line = (
            f"🛗 Статус ліфта: <b>зупинено</b> 🛑 (заряд нижче критичного порогу {int(crit_soc)}%)"
        )

    hrs_left = telemetry.estimate_hours_to_critical(house.battery_capacity_kwh)
    if hrs_left is not None and hrs_left > 0:
        eta_line = (
            f"⏳ До порогу відключення ліфта ({int(crit_soc)}%): "
            f"~<b>{format_duration_ua(hrs_left * 3600)}</b>"
        )
    elif telemetry.soc <= crit_soc:
        eta_line = f"⏳ Досягнуто критичний поріг ліфта ({int(crit_soc)}%)"
    else:
        eta_line = f"⏳ Запас до порогу ліфта ({int(crit_soc)}%): <b>високий (не розряджається)</b>"

    return (
        f"🏢 <b>{house.name}</b> • кв. {user.apartment}\n"
        f"────────────────────\n"
        f"{grid_line}\n"
        f"{sched_line}\n"
        f"{voltage_line}\n\n"
        f"🔋 Заряд батарей (SOC): <b>{telemetry.soc:.0f}%</b>\n"
        f"{bat_flow}\n"
        f"🏠 Споживання будинку: <b>{telemetry.load_power_kw:.2f} кВт</b>\n\n"
        f"{elevator_line}\n"
        f"{eta_line}\n"
        f"────────────────────\n"
        f"🕒 Оновлено: <code>{telemetry.updated_at}</code> <i>(інтервал ~5 хв)</i>"
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user, err = await _ensure_approved_user(message.from_user.id)
    if err:
        await message.answer(err)
        return

    telemetry = await _get_cached_or_fresh_telemetry(user.house)
    await message.answer(
        _build_status_text(user, telemetry),
        reply_markup=build_status_refresh_keyboard(),
    )


@router.callback_query(F.data == "refresh_status")
async def cb_refresh_status(callback: CallbackQuery) -> None:
    user, err = await _ensure_approved_user(callback.from_user.id)
    if err:
        await callback.answer(err, show_alert=True)
        return

    telemetry = await _get_cached_or_fresh_telemetry(user.house)
    text = _build_status_text(user, telemetry)
    try:
        await callback.message.edit_text(text, reply_markup=build_status_refresh_keyboard())
    except Exception:
        pass
    await callback.answer("Дані оновлено ✅")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    user, err = await _ensure_approved_user(message.from_user.id)
    if err:
        await message.answer(err)
        return

    text = dtek_service.format_schedule_message(
        house_name=user.house.name,
        dtek_group=user.house.dtek_group or "Черга 5.1",
    )
    await message.answer(text)


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    user, err = await _ensure_approved_user(message.from_user.id)
    if err:
        await message.answer(err)
        return

    async with async_session_factory() as session:
        logs = await get_outages_for_period(session, user.house.id, days=7)

    telemetry = await _get_cached_or_fresh_telemetry(user.house)
    now = datetime.now()

    lines = [
        f"📜 <b>Історія відключень за 7 днів — {user.house.name}</b>",
        "",
    ]

    if not logs:
        lines.append("🟢 За останні 7 днів зафіксованих відключень не було.")
    else:
        total_seconds = 0.0
        for log in logs:
            start_str = log.start_time.strftime("%d.%m %H:%M")
            if log.end_time:
                end_str = log.end_time.strftime("%H:%M")
                dur_sec = (log.end_time - log.start_time).total_seconds()
                end_soc_val = log.end_soc if log.end_soc is not None else telemetry.soc
            else:
                end_str = "триває"
                dur_sec = (now - log.start_time).total_seconds()
                end_soc_val = telemetry.soc

            total_seconds += max(0.0, dur_sec)
            dur_str = format_duration_ua(dur_sec)
            start_soc_val = log.start_soc if log.start_soc is not None else 100.0
            badge = "⚠️" if log.event_type == OutageEventType.emergency else "🔴"

            lines.append(
                f"{badge} <code>{start_str}–{end_str}</code>, {dur_str} • "
                f"заряд <b>{start_soc_val:.0f}% → {end_soc_val:.0f}%</b>"
            )

        lines.append("")
        lines.append(
            f"📊 <b>Всього відключень:</b> {len(logs)} | "
            f"<b>Сумарний час без світла:</b> {format_duration_ua(total_seconds)}"
        )

    await message.answer("\n".join(lines))


async def _send_charts(target_message: Message, user: User) -> None:
    wait_msg = await target_message.answer("⏳ Генерую актуальні графіки заряду та відключень...")
    async with async_session_factory() as session:
        telemetry_logs = await get_telemetry_for_hours(session, user.house.id, hours=24)
        outage_logs = await get_outages_for_period(session, user.house.id, days=7)

    png_bytes = generate_house_charts_png(
        house_name=user.house.name,
        telemetry_logs=telemetry_logs,
        outage_logs=outage_logs,
    )
    photo = BufferedInputFile(png_bytes, filename=f"charts_house_{user.house.id}.png")
    caption = (
        f"📊 <b>Графіки заряду батарей, споживання та відключень — {user.house.name}</b>\n"
        f"• Червона пунктирна лінія (20%) — поріг вимкнення ліфта.\n"
        f"• Оновлення телеметрії відбувається кожні 5 хвилин."
    )
    await target_message.answer_photo(photo=photo, caption=caption)
    try:
        await wait_msg.delete()
    except Exception:
        pass


@router.message(Command("charts"))
async def cmd_charts(message: Message) -> None:
    user, err = await _ensure_approved_user(message.from_user.id)
    if err:
        await message.answer(err)
        return
    await _send_charts(message, user)


@router.callback_query(F.data == "open_charts")
async def cb_open_charts(callback: CallbackQuery) -> None:
    user, err = await _ensure_approved_user(callback.from_user.id)
    if err:
        await callback.answer(err, show_alert=True)
        return
    await callback.answer()
    await _send_charts(callback.message, user)
