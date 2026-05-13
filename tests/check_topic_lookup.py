from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from _common import load_settings, parse_int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check support topic lookup.")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--topic-id", type=int, default=None)
    args = parser.parse_args()

    if args.user_id is None and os.getenv("TEST_USER_ID"):
        args.user_id = parse_int(os.getenv("TEST_USER_ID"), "TEST_USER_ID")
    if args.topic_id is None and os.getenv("TEST_TOPIC_ID"):
        args.topic_id = parse_int(os.getenv("TEST_TOPIC_ID"), "TEST_TOPIC_ID")
    if args.user_id is None and args.topic_id is None:
        parser.error("provide --user-id/--topic-id or TEST_USER_ID/TEST_TOPIC_ID")
    return args


async def check() -> int:
    args = parse_args()
    settings = load_settings()

    from database.repositories import get_by_topic_id, get_by_user_id

    engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            if args.user_id is not None:
                print(f"user_lookup_result: {await get_by_user_id(session, args.user_id)!r}")
            if args.topic_id is not None:
                print(f"topic_lookup_result: {await get_by_topic_id(session, args.topic_id)!r}")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
