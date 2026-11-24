import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

from app.bot.handlers import admin, monitoring, onboarding
from app.config import settings
from app.database.base import Base, engine
from app.services.monitor import EnergyMonitorWorker
from app.services.redis_cache import redis_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="status", description="Заряд батарей і стан ліфта"),
        BotCommand(command="schedule", description="Графік відключень ДТЕК"),
        BotCommand(command="history", description="Відключення за тиждень"),
        BotCommand(command="charts", description="Графіки заряду й відключень"),
        BotCommand(command="join", description="Запросити доступ до будинку"),
        BotCommand(command="settings", description="Які сповіщення надсилати"),
        BotCommand(command="leave", description="Вийти з будинку"),
        BotCommand(command="feedback", description="Повідомити про помилку"),
        BotCommand(command="help", description="Як користуватися ботом"),
    ]
    await bot.set_my_commands(commands)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema verified.")


async def main() -> None:
    await init_db()

    # Initialize FSM storage (Redis with fallback to MemoryStorage)
    try:
        await redis_cache.client.ping()
        storage = RedisStorage(redis=redis_cache.client)
        logger.info("Connected to Redis FSM storage.")
    except Exception as exc:
        logger.warning("Redis unavailable (%s), falling back to MemoryStorage.", exc)
        storage = MemoryStorage()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)

    # Register routers
    dp.include_router(admin.router)
    dp.include_router(onboarding.router)
    dp.include_router(monitoring.router)

    await set_bot_commands(bot)

    # Start background Deye Cloud monitoring worker
    monitor_worker = EnergyMonitorWorker(bot=bot)
    await monitor_worker.start()

    try:
        logger.info("Starting Telegram Bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await monitor_worker.stop()
        await redis_cache.close()
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
