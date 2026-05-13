from aiogram import Bot, Dispatcher

from bot.handlers import router
from config.settings import Settings


def create_bot(settings: Settings) -> Bot:
    return Bot(token=settings.bot_token)


def create_dispatcher(settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(settings=settings)
    dispatcher.include_router(router)
    return dispatcher
