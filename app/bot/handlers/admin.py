from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import build_admin_approval_keyboard
from app.config import settings
from app.database.base import async_session_factory
from app.database.crud import (
    approve_user,
    get_all_houses,
    get_pending_users,
    upsert_house,
)
from app.services.deye_api import deye_client

router = Router(name="admin")


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_list


@router.callback_query(F.data.startswith("admin_approve:"))
async def cb_admin_approve(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔️ Недостатньо прав адміністратора", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    async with async_session_factory() as session:
        user = await approve_user(session, user_id=user_id, approved=True)

    if not user:
        await callback.answer("Користувача не знайдено", show_alert=True)
        return

    house_name = user.house.name if user.house else "будинку"
    await callback.message.edit_text(
        f"{callback.message.html_text}\n\n"
        f"✅ <b>СХВАЛЕНО</b> адміністратором @{callback.from_user.username or callback.from_user.id}"
    )
    await callback.answer("Доступ надано ✅")

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=f"✅ Вам надано доступ до <b>{house_name}</b> (кв. {user.apartment}).\n"
            f"Тисніть /status для перегляду стану енергосистеми!",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_reject:"))
async def cb_admin_reject(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔️ Недостатньо прав адміністратора", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    async with async_session_factory() as session:
        user = await approve_user(session, user_id=user_id, approved=False)

    if not user:
        await callback.answer("Користувача не знайдено", show_alert=True)
        return

    await callback.message.edit_text(
        f"{callback.message.html_text}\n\n"
        f"❌ <b>ВІДХИЛЕНО</b> адміністратором @{callback.from_user.username or callback.from_user.id}"
    )
    await callback.answer("Запит відхилено ❌")

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text="❌ Ваш запит на доступ до будинку було відхилено адміністратором. "
            "Перевірте правильність номера квартири та спробуйте знову через /join.",
        )
    except Exception:
        pass


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔️ Ця команда доступна лише адміністраторам системи.")
        return

    async with async_session_factory() as session:
        houses = await get_all_houses(session)
        pending = await get_pending_users(session)

    house_lines = [
        f"• ID <code>{h.id}</code>: <b>{h.name}</b> (Deye: <code>{h.station_id}</code>, {h.dtek_group})"
        for h in houses
    ] or ["Немає доданих будинків"]

    await message.answer(
        "🛠 <b>Панель адміністратора</b>\n\n"
        f"📋 Заявок на підтвердження: <b>{len(pending)}</b> (/pending)\n"
        f"🏢 Будинків у системі: <b>{len(houses)}</b>\n"
        + "\n".join(house_lines)
        + "\n\n<b>Команди керування:</b>\n"
        "• /pending — Показати всі нерозглянуті заявки мешканців\n"
        "• /sync_houses — Синхронізувати станції з мастер-акаунту Deye Cloud\n"
        "• <code>/add_house station_id | Назва | Черга 5.1</code> — Додати/оновити будинок вручну"
    )


@router.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    async with async_session_factory() as session:
        pending = await get_pending_users(session)

    if not pending:
        await message.answer("✅ Немає нових заявок на підтвердження.")
        return

    for u in pending:
        house_name = u.house.name if u.house else "Невідомо"
        await message.answer(
            f"🔔 <b>Заявка очікує рішення</b>\n"
            f"👤 Мешканець: <b>{u.full_name}</b> (@{u.username or 'немає'})\n"
            f"🏢 Будинок: <b>{house_name}</b>\n"
            f"🚪 Квартира: <b>{u.apartment}</b>",
            reply_markup=build_admin_approval_keyboard(u.id),
        )


@router.message(Command("sync_houses"))
async def cmd_sync_houses(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    stations = await deye_client.get_stations()
    async with async_session_factory() as session:
        for st in stations:
            await upsert_house(
                session=session,
                station_id=str(st["id"]),
                name=st["name"],
                dtek_group=st.get("dtek_group", "Черга 5.1"),
            )

    await message.answer(
        f"✅ Успішно синхронізовано <b>{len(stations)}</b> будинків із мастер-акаунту Deye Cloud!"
    )


@router.message(Command("add_house"))
async def cmd_add_house(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    raw = (message.text or "").replace("/add_house", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        await message.answer(
            "Використання:\n<code>/add_house station_id | Будинок №61 | Черга 5.1</code>"
        )
        return

    station_id = parts[0]
    name = parts[1]
    dtek_group = parts[2] if len(parts) >= 3 else "Черга 5.1"

    async with async_session_factory() as session:
        house = await upsert_house(
            session=session,
            station_id=station_id,
            name=name,
            dtek_group=dtek_group,
        )

    await message.answer(
        f"✅ Будинок <b>{house.name}</b> (Station ID: <code>{house.station_id}</code>, {house.dtek_group}) збережено!"
    )
