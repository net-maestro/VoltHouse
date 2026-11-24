import re
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    build_admin_approval_keyboard,
    build_houses_keyboard,
    build_leave_confirm_keyboard,
    build_settings_keyboard,
)
from app.bot.states import FeedbackStates, OnboardingStates
from app.config import settings
from app.database.base import async_session_factory
from app.database.crud import (
    get_all_houses,
    get_house_by_id,
    get_or_create_user,
    leave_house,
    request_house_join,
    toggle_user_notification,
)

router = Router(name="onboarding")

APARTMENT_REGEX = re.compile(r"^[0-9]{1,4}[а-яА-Яa-zA-ZіїєґІЇЄҐ\-]{0,3}$")


@router.message(CommandStart())
@router.message(Command("join"))
async def cmd_start_or_join(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session=session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        houses = await get_all_houses(session)

    if user.house_id and user.is_approved and user.house:
        await message.answer(
            f"👋 Вітаємо, <b>{message.from_user.first_name}</b>!\n\n"
            f"Ви підключені до <b>{user.house.name}</b> (кв. <b>{user.apartment}</b>).\n"
            f"Використовуйте меню команд:\n"
            f"• /status — Заряд батарей і стан ліфта\n"
            f"• /schedule — Графік відключень ДТЕК\n"
            f"• /history — Відключення за тиждень\n"
            f"• /charts — Графіки заряду й відключень\n"
            f"• /settings — Налаштування сповіщень\n"
            f"• /leave — Змінити або вийти з будинку"
        )
        return

    if not houses:
        await message.answer(
            "👋 Вітаємо! Наразі список будинків синхронізується з хмарою Deye Cloud. "
            "Спробуйте за декілька хвилин або зверніться до адміністратора."
        )
        return

    await state.set_state(OnboardingStates.choosing_house)
    await message.answer(
        "👋 <b>Вітаємо в системі моніторингу енергосистеми будинку!</b>\n\n"
        "Оберіть ваш будинок зі списку нижче, щоб отримувати дані про заряд батарей, "
        "роботу ліфтів та сповіщення про світло:",
        reply_markup=build_houses_keyboard(houses),
    )


@router.callback_query(F.data.startswith("select_house:"))
async def cb_select_house(callback: CallbackQuery, state: FSMContext) -> None:
    house_id = int(callback.data.split(":")[1])
    async with async_session_factory() as session:
        house = await get_house_by_id(session, house_id)

    if not house:
        await callback.answer("Будинок не знайдено", show_alert=True)
        return

    await state.update_data(house_id=house.id, house_name=house.name)
    await state.set_state(OnboardingStates.entering_apartment)
    await callback.message.edit_text(
        f"🏢 Ви обрали: <b>{house.name}</b>\n\n"
        f"🔢 Вкажіть номер вашої квартири (наприклад: <code>64</code> або <code>12а</code>):"
    )
    await callback.answer()


@router.message(OnboardingStates.entering_apartment)
async def process_apartment_input(message: Message, state: FSMContext, bot: Bot) -> None:
    apartment = (message.text or "").strip()
    if not APARTMENT_REGEX.match(apartment):
        await message.answer(
            "⚠️ Будь ласка, введіть коректний номер квартири (наприклад, <code>64</code> або <code>12а</code>):"
        )
        return

    data = await state.get_data()
    house_id = data.get("house_id")
    house_name = data.get("house_name", "Будинок")

    async with async_session_factory() as session:
        user = await request_house_join(
            session=session,
            telegram_id=message.from_user.id,
            house_id=house_id,
            apartment=apartment,
        )

    await state.clear()
    await message.answer(
        f"📨 Запит на доступ до <b>{house_name}</b> (кв. <b>{apartment}</b>) надіслано адміністратору.\n"
        f"Ви отримаєте сповіщення одразу після підтвердження!"
    )

    # Notify all configured admins
    if user:
        user_mention = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else message.from_user.full_name
        )
        admin_text = (
            f"🔔 <b>Новий запит на доступ до будинку</b>\n\n"
            f"👤 Мешканець: <b>{message.from_user.full_name}</b> ({user_mention})\n"
            f"🆔 Telegram ID: <code>{message.from_user.id}</code>\n"
            f"🏢 Будинок: <b>{house_name}</b>\n"
            f"🚪 Квартира: <b>{apartment}</b>"
        )
        for admin_id in settings.admin_id_list:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=build_admin_approval_keyboard(user.id),
                )
            except Exception:
                pass


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session=session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )

    await message.answer(
        "⚙️ <b>Налаштування персональних сповіщень</b>\n\n"
        "Оберіть, які сповіщення ви бажаєте отримувати від бота:",
        reply_markup=build_settings_keyboard(user),
    )


@router.callback_query(F.data.startswith("toggle_pref:"))
async def cb_toggle_pref(callback: CallbackQuery) -> None:
    field = callback.data.split(":")[1]
    async with async_session_factory() as session:
        user = await toggle_user_notification(session, callback.from_user.id, field)

    if not user:
        await callback.answer("Помилка оновлення налаштувань", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=build_settings_keyboard(user))
    await callback.answer("Налаштування збережено ✅")


@router.message(Command("leave"))
async def cmd_leave(message: Message) -> None:
    async with async_session_factory() as session:
        user = await get_or_create_user(session, message.from_user.id)

    if not user.house_id:
        await message.answer("ℹ️ Ви наразі не прив'язані до жодного будинку. Натисніть /join.")
        return

    house_title = user.house.name if user.house else "поточного будинку"
    await message.answer(
        f"❓ Ви дійсно бажаєте вийти з <b>{house_title}</b> (кв. {user.apartment})?\n"
        f"Після виходу для повторного підключення знадобиться нове підтвердження адміністратора.",
        reply_markup=build_leave_confirm_keyboard(),
    )


@router.callback_query(F.data.startswith("confirm_leave:"))
async def cb_confirm_leave(callback: CallbackQuery) -> None:
    decision = callback.data.split(":")[1]
    if decision == "yes":
        async with async_session_factory() as session:
            await leave_house(session, callback.from_user.id)
        await callback.message.edit_text(
            "✅ Ви вийшли з будинку. Щоб приєднатися знову, скористайтеся командою /join."
        )
    else:
        await callback.message.edit_text("👍 Дію скасовано. Ви залишаєтесь підключеними до будинку.")
    await callback.answer()


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext) -> None:
    await state.set_state(FeedbackStates.entering_message)
    await message.answer(
        "📝 <b>Зв'язок із адміністратором</b>\n\n"
        "Опишіть помилку, неточність у показниках або вашу пропозицію одним повідомленням "
        "(або натисніть /cancel для скасування):"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Дію скасовано.")


@router.message(FeedbackStates.entering_message)
async def process_feedback(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Будь ласка, надішліть текстове повідомлення.")
        return

    await state.clear()
    async with async_session_factory() as session:
        user = await get_or_create_user(session, message.from_user.id)

    house_info = (
        f"{user.house.name}, кв. {user.apartment}" if user and user.house else "Не прив'язаний"
    )
    feedback_msg = (
        f"📩 <b>Звернення через /feedback</b>\n"
        f"👤 Від: <b>{message.from_user.full_name}</b> (@{message.from_user.username or 'немає'})\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"🏢 Будинок: {house_info}\n\n"
        f"💬 <b>Повідомлення:</b>\n{text}"
    )

    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(chat_id=admin_id, text=feedback_msg)
        except Exception:
            pass

    await message.answer("✅ Дякуємо! Ваше повідомлення надіслано адміністратору.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📖 <b>Як користуватися ботом енергомоніторингу</b>\n\n"
        "Бот у реальному часі відстежує стан інверторів <b>Deye</b>, заряд акумуляторів (АКБ), "
        "роботу ліфтів і насосів, а також графіки ДТЕК.\n\n"
        "<b>Основні команди:</b>\n"
        "• /status — Поточний стан мережі, напруга по 3 фазах, заряд батарей (SOC %), "
        "статус ліфта та час роботи до критичного порогу (20%).\n"
        "• /schedule — Планові черги та години відключень ДТЕК на сьогодні й завтра.\n"
        "• /history — Детальний журнал відключень за останні 7 днів із динамікою розряду АКБ.\n"
        "• /charts — Візуальні графіки заряду батарей, споживання будинку (кВт) та відключень.\n"
        "• /settings — Увімкнення/вимкнення типів сповіщень.\n"
        "• /join — Запросити доступ до будинку.\n"
        "• /leave — Від'єднатися від будинку.\n"
        "• /feedback — Повідомити адміністратора про проблему.\n\n"
        "💡 <i>Дані з інверторів оновлюються автоматично кожні 5 хвилин.</i>"
    )
