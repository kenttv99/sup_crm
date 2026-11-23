import socket

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from bot.handlers import router
from config.settings import Settings


def create_bot(settings: Settings) -> Bot:
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    return Bot(token=settings.bot_token, session=session)


def create_dispatcher(settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(settings=settings)
    dispatcher.include_router(router)
    return dispatcher
