from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from _common import async_database_url, load_dotenv, load_settings, parse_int, settings_to_dict


REPOSITORY_MODULES = (
    "database.repositories",
    "database.repositories.topics",
    "database.repositories.topic_repository",
    "repositories.topics",
    "repositories.topic_repository",
    "bot.repositories.topics",
)

USER_LOOKUP_NAMES = (
    "get_by_user_id",
    "find_by_user_id",
    "get_topic_by_user_id",
    "get_support_topic_by_user_id",
)

TOPIC_LOOKUP_NAMES = (
    "get_by_topic_id",
    "find_by_topic_id",
    "get_user_by_topic_id",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check support topic repository lookup.")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--topic-id", type=int, default=None)
    parser.add_argument("values", nargs="*", help="Optional positional user_id topic_id")
    args = parser.parse_args()

    if args.values:
        if len(args.values) > 2:
            parser.error("expected at most two positional values: user_id topic_id")
        if args.user_id is None and len(args.values) >= 1:
            args.user_id = parse_int(args.values[0], "user_id")
        if args.topic_id is None and len(args.values) == 2:
            args.topic_id = parse_int(args.values[1], "topic_id")

    if args.user_id is None and os.getenv("TEST_USER_ID"):
        args.user_id = parse_int(os.getenv("TEST_USER_ID"), "TEST_USER_ID")
    if args.topic_id is None and os.getenv("TEST_TOPIC_ID"):
        args.topic_id = parse_int(os.getenv("TEST_TOPIC_ID"), "TEST_TOPIC_ID")

    if args.user_id is None and args.topic_id is None:
        parser.error("provide --user-id/--topic-id or TEST_USER_ID/TEST_TOPIC_ID")
    return args


def repository_modules() -> list[Any]:
    modules = []
    for module_name in REPOSITORY_MODULES:
        try:
            modules.append(importlib.import_module(module_name))
        except Exception:
            continue
    return modules


def repository_candidates(modules: list[Any]) -> list[type[Any]]:
    classes: list[type[Any]] = []
    for module in modules:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if "repo" in obj.__name__.lower() or "topic" in obj.__name__.lower():
                classes.append(obj)
    return classes


def instantiate(repo_cls: type[Any], session: AsyncSession) -> Any | None:
    for args in ((session,), ()):
        try:
            return repo_cls(*args)
        except TypeError:
            continue
        except Exception:
            return None
    return None


async def call_lookup(repo: Any, names: tuple[str, ...], value: int) -> tuple[str, Any] | None:
    for name in names:
        method = getattr(repo, name, None)
        if not callable(method):
            continue
        result = method(value)
        if inspect.isawaitable(result):
            result = await result
        return name, result
    return None


async def call_module_lookup(
    module: Any,
    names: tuple[str, ...],
    session: AsyncSession,
    value: int,
) -> tuple[str, Any] | None:
    for name in names:
        func = getattr(module, name, None)
        if not callable(func):
            continue
        result = func(session, value)
        if inspect.isawaitable(result):
            result = await result
        return name, result
    return None


async def fallback_db_lookup(session: AsyncSession, user_id: int | None, topic_id: int | None) -> None:
    columns = {
        row[0]
        for row in (
            await session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'support_topics'
                    """
                )
            )
        ).all()
    }
    if not columns:
        print("repository: not found")
        print("support_topics_table: missing")
        return

    conditions = []
    params: dict[str, int] = {}
    if user_id is not None and "user_id" in columns:
        conditions.append("user_id = :user_id")
        params["user_id"] = user_id
    if topic_id is not None and "topic_id" in columns:
        conditions.append("topic_id = :topic_id")
        params["topic_id"] = topic_id

    if not conditions:
        print("repository: not found")
        print(f"support_topics_columns: {sorted(columns)}")
        print("lookup_columns: no compatible user_id/topic_id columns")
        return

    where_sql = " OR ".join(conditions)
    rows = (
        await session.execute(
            text(f"SELECT * FROM support_topics WHERE {where_sql} LIMIT 10"),
            params,
        )
    ).mappings().all()
    print("repository: not found; used direct support_topics query")
    print(f"rows_found: {len(rows)}")
    for row in rows:
        print(dict(row))


async def check() -> int:
    load_dotenv()
    args = parse_args()
    settings_obj, _ = load_settings()
    settings = settings_to_dict(settings_obj)
    url = async_database_url(settings)
    if not url:
        print("database_url: missing")
        return 1

    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            modules = repository_modules()
            for module in modules:
                matched = False
                if args.user_id is not None:
                    result = await call_module_lookup(module, USER_LOOKUP_NAMES, session, args.user_id)
                    if result:
                        matched = True
                        print(f"repository_module: {module.__name__}")
                        print(f"user_lookup_function: {result[0]}")
                        print(f"user_lookup_result: {result[1]!r}")
                if args.topic_id is not None:
                    result = await call_module_lookup(module, TOPIC_LOOKUP_NAMES, session, args.topic_id)
                    if result:
                        matched = True
                        print(f"repository_module: {module.__name__}")
                        print(f"topic_lookup_function: {result[0]}")
                        print(f"topic_lookup_result: {result[1]!r}")
                if matched:
                    return 0

            for repo_cls in repository_candidates(modules):
                repo = instantiate(repo_cls, session)
                if repo is None:
                    continue
                print(f"repository: {repo_cls.__module__}.{repo_cls.__name__}")
                matched = False
                if args.user_id is not None:
                    result = await call_lookup(repo, USER_LOOKUP_NAMES, args.user_id)
                    if result:
                        matched = True
                        print(f"user_lookup_method: {result[0]}")
                        print(f"user_lookup_result: {result[1]!r}")
                if args.topic_id is not None:
                    result = await call_lookup(repo, TOPIC_LOOKUP_NAMES, args.topic_id)
                    if result:
                        matched = True
                        print(f"topic_lookup_method: {result[0]}")
                        print(f"topic_lookup_result: {result[1]!r}")
                if matched:
                    return 0

            await fallback_db_lookup(session, args.user_id, args.topic_id)
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
