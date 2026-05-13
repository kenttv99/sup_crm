# MVP Plan: CRM Support Bot

## Goal

Build a Telegram support bot that receives user messages, keeps a CRM-facing support thread per Telegram user, stores conversation state in PostgreSQL, and exposes Telegram updates through a FastAPI webhook.

## Architecture

- Telegram Bot API integration: `aiogram`.
- HTTP entrypoint: `FastAPI` webhook endpoint for Telegram updates.
- Runtime server: `uvicorn`.
- Primary database: PostgreSQL database `sup_crm`.
- ORM and migrations: SQLAlchemy with Alembic.
- Database access model: both sync and async SQLAlchemy engines are supported.
- Cache or transient state: Redis.
- Configuration: `.env` for local secrets and `.env.example` for the documented variable set.

## Core Flow

1. Telegram sends an update to the FastAPI webhook endpoint.
2. FastAPI validates the webhook secret and passes the update to `aiogram`.
3. The bot identifies the Telegram user.
4. The CRM layer loads or creates one support topic for that user.
5. User/topic mapping is stored in PostgreSQL; message history stays in Telegram topics.
6. Support replies in the topic are mapped back to the user and sent through the bot.

## Topic Model

- One Telegram support topic is created per user.
- The mapping between user and topic is persisted in PostgreSQL.
- Repeated user messages reuse the existing topic.
- Topic metadata should include Telegram user id, topic/message thread id, creation timestamp, and status.

## Topic Lifecycle Contract

- `status=open`: active appeal. The information header for the current appeal must already exist, so repeated user messages only get copied to the topic.
- `status=closed`: closed appeal. Operator messages from that topic are ignored by the relay.
- New database row: create Telegram topic, send one information header, pin it, then store `status=open`.
- Existing row with `status=closed`: treat the next private user message as a new appeal in the existing topic, switch status to `open`, send one new information header, unpin previous topic pins, and pin the new header.
- Existing row with `status=open`: do not send or pin a new information header.
- Deleted Telegram topic: create a replacement topic, update the stored `topic_id`, set `status=open`, send and pin one new information header.
- Closing can be done from the inline topic button or with `/end` inside the topic; both paths are idempotent and update the database status.

## Database

- Database name: `sup_crm`.
- Local DSN:
  `postgresql+psycopg://postgres:assasin88@localhost:5432/sup_crm`
- SQLAlchemy sync engine is used for scripts, migrations-adjacent tooling, and simple administrative tasks.
- SQLAlchemy async engine is used for bot and webhook runtime paths. On Windows local development, use `postgresql+asyncpg` for `ASYNC_DATABASE_URL`.
- Alembic owns schema migrations.

## Configuration

Required environment variables:

- `BOT_TOKEN`: Telegram bot token. If it is missing or still contains placeholder text, webhook startup stops with a readable startup error.
- `SUPPORT_CHAT_ID`: Telegram support group or supergroup id. Use numeric `0` only as a local placeholder; before real startup replace it with the actual negative supergroup id.
- `DATABASE_URL`: default PostgreSQL SQLAlchemy DSN.
- `SYNC_DATABASE_URL`: sync SQLAlchemy DSN.
- `ASYNC_DATABASE_URL`: async SQLAlchemy DSN, for example `postgresql+asyncpg://...`.
- `REDIS_URL`: Redis connection URL.
- `WEBHOOK_BASE_URL`: public or local webhook base URL.
- `WEBHOOK_PATH`: Telegram webhook path.
- `WEBHOOK_SECRET_TOKEN`: Telegram webhook secret token.
- `DROP_PENDING_UPDATES`: whether Telegram should drop queued updates when registering the webhook.
- `ADMIN_IDS`: comma-separated Telegram admin ids.
- `SQL_ECHO`: SQLAlchemy SQL logging flag.
- `APP_HOST`: FastAPI bind host.
- `APP_PORT`: FastAPI bind port.
- `LOG_LEVEL`: runtime logging level.

## Local Environment

- `.env` contains local values and must not be committed.
- `.env.example` contains the full variable list with placeholders.
- `.gitignore` excludes `.env`, virtual environments, caches, IDE metadata, and logs while keeping `.env.example` trackable.
- `python main.py` uses `APP_HOST` and `APP_PORT` from `.env`.
- `uvicorn main:app --reload` uses uvicorn CLI defaults unless `--host` and `--port` are passed explicitly.

## Dependencies

Runtime and infrastructure dependencies live in `requirements.txt`:

- `aiogram`
- `SQLAlchemy`
- `alembic`
- `redis`
- `fastapi`
- `uvicorn`
- `psycopg[binary]`
- `pydantic-settings`
- `httpx`
- `asyncpg`

## Test And Utility Scripts

Add focused scripts for local validation:

- database connectivity check using `SYNC_DATABASE_URL`;
- async database connectivity check using `ASYNC_DATABASE_URL`;
- Redis connectivity check using `REDIS_URL`;
- webhook registration script using `BOT_TOKEN`, `WEBHOOK_BASE_URL`, `WEBHOOK_PATH`, and `WEBHOOK_SECRET_TOKEN`;
- smoke script that verifies bot identity through Telegram Bot API.

Scripts should fail fast, print only actionable diagnostics, and avoid mutating production data.
