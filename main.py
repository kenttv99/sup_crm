from contextlib import asynccontextmanager
import asyncio
import sys

import uvicorn
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status

from bot.factory import create_bot, create_dispatcher
from config.settings import get_settings


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_telegram_webhook_configured()
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret_token,
            drop_pending_updates=settings.drop_pending_updates,
        )
    except WebhookStartupError as exc:
        print(f"\nWebhook startup error: {exc}\n", file=sys.stderr)
        await bot.session.close()
        raise WebhookStartupError(str(exc)) from None
    except TelegramUnauthorizedError:
        message = "Telegram rejected BOT_TOKEN while setting webhook. Check BOT_TOKEN in .env."
        print(f"\nWebhook startup error: {message}\n", file=sys.stderr)
        await bot.session.close()
        raise WebhookStartupError(message) from None
    try:
        yield
    finally:
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(settings.webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if x_telegram_bot_api_secret_token != settings.webhook_secret_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_webhook_update(bot, update)
    return {"ok": True}


def main() -> None:
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
