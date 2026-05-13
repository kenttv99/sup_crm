from contextlib import asynccontextmanager
import asyncio
import sys

import uvicorn
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status

from bot.factory import create_bot, create_dispatcher
from config.settings import get_settings


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

settings = get_settings()
bot = create_bot(settings)
dispatcher = create_dispatcher(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret_token,
        drop_pending_updates=settings.drop_pending_updates,
    )
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
