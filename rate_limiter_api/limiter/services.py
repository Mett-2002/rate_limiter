"""Redis connection, queue identity and rolling-window decisions."""

import hashlib
import math
from functools import lru_cache
from pathlib import Path

import redis
from django.conf import settings


LUA_SCRIPT = Path(__file__).with_name("sliding_window.lua").read_text(
    encoding="utf-8"
)


@lru_cache(maxsize=4)
def redis_backend(url):
    """Reuse a thread-safe Redis connection pool instead of reconnecting per call."""
    client = redis.Redis.from_url(
        url,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )
    return client, client.register_script(LUA_SCRIPT)


def _history_key(api_client, user_id):
    # Hashing avoids putting raw user identifiers into Redis key names.
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    # Keep queue names stable so existing counters remain valid.
    return (
        f"{settings.REDIS_KEY_PREFIX}:service:{api_client.pk}:"
        f"policy:{api_client.policy_version}:user:{user_hash}"
    )


def check_limit(api_client, user_id, *, consume_request=True, now_us=None):
    """now_us is ONLY for deterministic Python tests, never a request parameter."""
    if api_client.pk is None:
        raise ValueError("Save the API client before checking its quota.")
    if api_client.max_requests < 1 or api_client.window_seconds < 1:
        raise ValueError("The rate-limit rule must contain positive values.")

    _, script = redis_backend(settings.REDIS_URL)
    allowed, used, remaining, wait_us, next_us = script(
        keys=[_history_key(api_client, user_id)],
        args=[
            api_client.max_requests,
            api_client.window_seconds * 1_000_000,
            int(consume_request),
            "" if now_us is None else now_us,
        ],
    )
    return {
        "allowed": bool(allowed),
        "limit": api_client.max_requests,
        "window_seconds": api_client.window_seconds,
        "used": used,
        "remaining": remaining,
        "retry_after": 0 if allowed else math.ceil(wait_us / 1_000_000),
        "next_available_at": next_us / 1_000_000 if next_us else None,
    }
