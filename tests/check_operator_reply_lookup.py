from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from _common import load_settings, parse_int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check operator reply topic lookup.")
    parser.add_argument("--topic-id", type=int, default=None)
    args = parser.parse_args()

    if args.topic_id is None and os.getenv("TEST_TOPIC_ID"):
        args.topic_id = parse_int(os.getenv("TEST_TOPIC_ID"), "TEST_TOPIC_ID")
    if args.topic_id is None:
        parser.error("provide --topic-id or TEST_TOPIC_ID")
    return args


def print_topic(topic: object) -> None:
    print("operator_reply_lookup: found")
    print(f"id: {getattr(topic, 'id', None)}")
    print(f"user_id: {getattr(topic, 'user_id', None)}")
    print(f"topic_id: {getattr(topic, 'topic_id', None)}")
    print(f"username: {getattr(topic, 'username', None)}")
    print(f"full_name: {getattr(topic, 'full_name', None)}")
    print(f"status: {getattr(topic, 'status', None)}")
    print(f"created_at: {getattr(topic, 'created_at', None)}")
    print(f"updated_at: {getattr(topic, 'updated_at', None)}")


async def check() -> int:
    args = parse_args()
    settings = load_settings()

    from database.repositories import get_by_topic_id

    engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            topic = await get_by_topic_id(session, args.topic_id)
            if topic is None:
                print(f"operator_reply_lookup: not_found topic_id={args.topic_id}")
                return 1
            print_topic(topic)
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
