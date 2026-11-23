from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from config.settings import get_settings

logger = logging.getLogger(__name__)

MSG_LINK_TTL_SEC = 60 * 60 * 24 * 30
START_PAYLOAD_TTL_SEC = 60 * 60 * 24
REOPEN_MSG_TTL_SEC = 60 * 60 * 24 * 30

_client: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def save_message_link(
    user_chat_id: int,
    user_msg_id: int,
    support_chat_id: int,
    topic_msg_id: int,
) -> None:
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.setex(
            f"msg_link:{user_chat_id}:{user_msg_id}",
            MSG_LINK_TTL_SEC,
            str(topic_msg_id),
        )
        pipe.setex(
            f"msg_link:{support_chat_id}:{topic_msg_id}",
            MSG_LINK_TTL_SEC,
            str(user_msg_id),
        )
        await pipe.execute()
    except RedisError as exc:
        logger.warning("Redis save_message_link failed: %s", exc)


async def get_linked_message_id(chat_id: int, msg_id: int) -> Optional[int]:
    try:
        value = await get_redis().get(f"msg_link:{chat_id}:{msg_id}")
        return int(value) if value is not None else None
    except (RedisError, ValueError) as exc:
        logger.warning("Redis get_linked_message_id failed: %s", exc)
        return None


async def check_rate_limit(
    user_id: int,
    *,
    limit: int = 12,
    window_sec: int = 10,
) -> bool:
    """Return True if request is allowed, False if rate limit exceeded."""
    key = f"rl:user:{user_id}"
    try:
        r = get_redis()
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, window_sec)
        return count <= limit
    except RedisError as exc:
        logger.warning("Redis check_rate_limit failed: %s", exc)
        return True


async def set_start_payload(user_id: int, payload: str) -> None:
    try:
        await get_redis().setex(
            f"start_payload:{user_id}",
            START_PAYLOAD_TTL_SEC,
            payload[:255],
        )
    except RedisError as exc:
        logger.warning("Redis set_start_payload failed: %s", exc)


async def pop_start_payload(user_id: int) -> Optional[str]:
    key = f"start_payload:{user_id}"
    try:
        r = get_redis()
        value = await r.get(key)
        if value is not None:
            await r.delete(key)
        return value
    except RedisError as exc:
        logger.warning("Redis pop_start_payload failed: %s", exc)
        return None


async def set_last_reopen_msg(user_id: int, message_id: int) -> None:
    try:
        await get_redis().setex(
            f"reopen_msg:{user_id}",
            REOPEN_MSG_TTL_SEC,
            str(message_id),
        )
    except RedisError as exc:
        logger.warning("Redis set_last_reopen_msg failed: %s", exc)


async def pop_last_reopen_msg(user_id: int) -> Optional[int]:
    key = f"reopen_msg:{user_id}"
    try:
        r = get_redis()
        value = await r.get(key)
        if value is not None:
            await r.delete(key)
            return int(value)
        return None
    except (RedisError, ValueError) as exc:
        logger.warning("Redis pop_last_reopen_msg failed: %s", exc)
        return None
