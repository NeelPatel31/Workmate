from typing import AsyncGenerator

import redis.asyncio as aioredis

from app.config import settings
from app.utils.custom_logger import logger


STREAM_PREFIX = "stream:"
SESSION_PREFIX = "session:"
THREAD_STATUS_PREFIX = "thread-status:"
STREAM_TTL_SECONDS = 300
THREAD_TTL_SECONDS = 400
DONE_TOKEN = "[DONE]"

_redis: aioredis.Redis | None = None


async def connect_redis() -> None:
    global _redis
    _redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    await _redis.ping()
    logger.info(f"Connected to Redis at {settings.redis_url}")


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        logger.info("Redis connection closed")
    _redis = None


async def redis_health() -> bool:
    global _redis

    if _redis is None:
        return False

    try:
        return await _redis.ping()
    except Exception:
        logger.exception("Redis health check failed")
        return False


def _stream_key(prompt_id: str) -> str:
    return f"{STREAM_PREFIX}{prompt_id}"


def _thread_status_key(session_id: str) -> str:
    return f"{THREAD_STATUS_PREFIX}{session_id}"


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


async def set_thread_status(session_id: str, status: str) -> None:
    """Persist the current execution status for a LangGraph thread."""
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    await _redis.set(_thread_status_key(session_id), status, ex=THREAD_TTL_SECONDS)


async def get_thread_status(session_id: str) -> str | None:
    """Return the stored execution status, or ``None`` for an unknown thread."""
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    return await _redis.get(_thread_status_key(session_id))


async def register_prompt(session_id: str, prompt_id: str) -> None:
    """Record that a prompt_id belongs to a session_id."""
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    key = _session_key(session_id)
    await _redis.sadd(key, prompt_id)
    await _redis.expire(key, STREAM_TTL_SECONDS)


async def prompt_belongs_to_session(session_id: str, prompt_id: str) -> bool:
    """Return True if prompt_id was registered under session_id."""
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    return bool(await _redis.sismember(_session_key(session_id), prompt_id))


async def add_token(prompt_id: str, token: str) -> None:
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    key = _stream_key(prompt_id)
    await _redis.xadd(key, {"token": token})


async def mark_done(prompt_id: str) -> None:
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    key = _stream_key(prompt_id)
    await _redis.xadd(key, {"token": DONE_TOKEN})
    await _redis.expire(key, STREAM_TTL_SECONDS)
    logger.info(f"Marked stream '{key}' as DONE with TTL={STREAM_TTL_SECONDS}s")


async def stream_exists(prompt_id: str) -> bool:
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    key = _stream_key(prompt_id)
    return bool(await _redis.exists(key))


async def read_stream(
    prompt_id: str,
    block_ms: int = 100,
    start_id: str = "0-0",
) -> AsyncGenerator[tuple[str, str], None]:
    if _redis is None:
        raise RuntimeError("Redis is not initialized. Call connect_redis() first.")

    key = _stream_key(prompt_id)
    last_id = start_id

    while True:
        entries = await _redis.xread({key: last_id}, count=100, block=block_ms)

        if not entries:
            exists = await _redis.exists(key)
            if not exists:
                logger.warning(f"Stream '{key}' does not exist (yet or expired)")
                continue
            break

        for _stream_name, messages in entries:
            for entry_id, fields in messages:
                last_id = entry_id
                token = fields.get("token", "")

                if token == DONE_TOKEN:
                    logger.info(f"Received DONE signal for stream '{key}'")
                    return

                yield entry_id, token
