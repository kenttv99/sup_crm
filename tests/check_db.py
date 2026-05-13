from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from _common import load_settings, mask_value


async def check() -> int:
    settings = load_settings()
    url = settings.async_database_url

    print(f"database_url: {mask_value('ASYNC_DATABASE_URL', url)}")
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            value = await conn.scalar(text("SELECT 1"))
            print(f"select_1: {value}")
            exists = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'support_topics'
                    )
                    """
                )
            )
            print(f"support_topics_table: {'present' if exists else 'missing'}")
            return 0 if value == 1 and exists else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
