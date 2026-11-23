import asyncio
from contextlib import asynccontextmanager
import sys
from typing import Dict, Optional

import uvicorn
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status

from bot.errors import SupportChatConfigError, SupportTopicLifecycleError
from bot.factory import create_bot, create_dispatcher
from bot.services.redis_cache import close_redis
from bot.services.topics import validate_support_chat
from config.settings import get_settings


settings = get_settings()
bot = create_bot(settings)
dispatcher = create_dispatcher(settings)


class WebhookStartupError(RuntimeError):
    pass


def ensure_telegram_webhook_configured() -> None:
    if "replace_with" in settings.bot_token:
        raise WebhookStartupError(
            "BOT_TOKEN is not configured. Set a real Telegram bot token in .env before webhook startup."
        )
    if settings.support_chat_id == 0:
        raise WebhookStartupError(
            "SUPPORT_CHAT_ID is not configured. Set the real Telegram support supergroup id in .env."
        )


async def _startup_telegram(settings_obj) -> None:
    for attempt in range(10):
        try:
            await validate_support_chat(bot, settings_obj.support_chat_id)
            break
        except TelegramRetryAfter as exc:
            print(
                f"Telegram FloodWait on validate_support_chat, retrying in {exc.retry_after + 2}s...",
                file=sys.stderr,
            )
            await asyncio.sleep(exc.retry_after + 2)
        except SupportChatConfigError as exc:
            print(f"\nSupport chat config error: {exc}\n", file=sys.stderr)
            return

    for attempt in range(10):
        try:
            await bot.set_webhook(
                url=settings_obj.webhook_url,
                secret_token=settings_obj.webhook_secret_token,
                drop_pending_updates=settings_obj.drop_pending_updates,
            )
            print(f"Telegram webhook registered: {settings_obj.webhook_url}", file=sys.stderr)
            return
        except TelegramRetryAfter as exc:
            print(
                f"Telegram FloodWait on set_webhook, retrying in {exc.retry_after + 2}s...",
                file=sys.stderr,
            )
            await asyncio.sleep(exc.retry_after + 2)
        except (TelegramUnauthorizedError, TelegramBadRequest) as exc:
            print(f"\nWebhook startup error: {exc}\n", file=sys.stderr)
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_telegram_webhook_configured()
    except WebhookStartupError as exc:
        print(f"\nWebhook startup error: {exc}\n", file=sys.stderr)
        await bot.session.close()
        raise WebhookStartupError(str(exc)) from None

    if not settings.admin_ids:
        print(
            "WARNING: ADMIN_IDS is empty — any member of SUPPORT_CHAT_ID can act as operator.",
            file=sys.stderr,
        )

    startup_task = asyncio.create_task(_startup_telegram(settings))
    try:
        yield
    finally:
        startup_task.cancel()
        await close_redis()
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post(settings.webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> Dict[str, bool]:
    if x_telegram_bot_api_secret_token != settings.webhook_secret_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    update = Update.model_validate(await request.json(), context={"bot": bot})
    try:
        await dispatcher.feed_webhook_update(bot, update)
    except (SupportChatConfigError, SupportTopicLifecycleError) as exc:
        print(f"Webhook domain error: {exc}", file=sys.stderr)
        return {"ok": False}
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        print(f"Webhook Telegram error: {exc}", file=sys.stderr)
        return {"ok": False}
    return {"ok": True}


def main() -> None:
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
