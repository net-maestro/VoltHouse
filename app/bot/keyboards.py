from typing import List
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.models import House, User


def build_houses_keyboard(houses: List[House]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for house in houses:
        builder.row(
            InlineKeyboardButton(
                text=f"🏢 {house.name} ({house.dtek_group or 'ДТЕК'})",
                callback_data=f"select_house:{house.id}",
            )
        )
    return builder.as_markup()


def build_admin_approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Підтвердити доступ",
            callback_data=f"admin_approve:{user_id}",
        ),
        InlineKeyboardButton(
            text="❌ Відхилити",
            callback_data=f"admin_reject:{user_id}",
        ),
    )
    return builder.as_markup()


def build_settings_keyboard(user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    outage_icon = "✅" if user.notify_outages else "🔕"
    voltage_icon = "✅" if user.notify_voltage else "🔕"
    soc_icon = "✅" if user.notify_critical_soc else "🔕"

    builder.row(
        InlineKeyboardButton(
            text=f"{outage_icon} Світло (відключення / повернення)",
            callback_data="toggle_pref:notify_outages",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{voltage_icon} Напруга (просідання / стабілізація)",
            callback_data="toggle_pref:notify_voltage",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{soc_icon} Критичний заряд АКБ та ліфт (< 20%)",
            callback_data="toggle_pref:notify_critical_soc",
        )
    )
    return builder.as_markup()


def build_leave_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚪 Так, вийти з будинку",
            callback_data="confirm_leave:yes",
        ),
        InlineKeyboardButton(
            text="Скасувати",
            callback_data="confirm_leave:no",
        ),
    )
    return builder.as_markup()


def build_status_refresh_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Оновити статус", callback_data="refresh_status"),
        InlineKeyboardButton(text="📊 Графіки", callback_data="open_charts"),
    )
    return builder.as_markup()
