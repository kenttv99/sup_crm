from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from _common import async_database_url, load_dotenv, load_settings, mask_value, settings_to_dict


async def check() -> int:
    load_dotenv()
    settings_obj, _ = load_settings()
    settings = settings_to_dict(settings_obj)
    url = async_database_url(settings)
    if not url:
        print("database_url: missing")
        return 1

    print(f"database_url: {mask_value('DATABASE_URL', url)}")
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
