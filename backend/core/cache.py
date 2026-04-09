"""Async cache layer — Redis when REDIS_URL is set, in-memory fallback otherwise."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from core.config import settings

logger = logging.getLogger("rxbuddy.cache")

# ── Per-source TTLs (seconds) ───────────────────────────────────────────────
ANSWER_TTL = 3600  # 1 hour for generated answers
EXT_TTLS: dict[str, int] = {
    "adverse_events": 21600,   # 6 hours  — FAERS data changes infrequently
    "recalls":        3600,    # 1 hour   — recalls can be added any time
    "rxnav_interact": 86400,   # 24 hours — DrugBank data is stable
    "medlineplus":    43200,   # 12 hours — MedlinePlus updated periodically
}


# ── Memory cache (fallback) ─────────────────────────────────────────────────

class MemoryCache:
    """In-memory dict cache with TTL. Used when REDIS_URL is not set."""

    def __init__(self, max_entries: int = 500, evict_count: int = 100):
        self._store: dict[str, tuple[Any, float]] = {}
        self._max = max_entries
        self._evict = evict_count

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        data, expires_at = entry
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return data

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        if len(self._store) >= self._max:
            for k in list(self._store.keys())[: self._evict]:
                self._store.pop(k, None)
        self._store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


# ── Redis cache ──────────────────────────────────────────────────────────────

class RedisCache:
    """Redis-backed async cache using redis.asyncio."""

    def __init__(self, url: str):
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        serialised = json.dumps(value, default=str)
        await self._redis.set(key, serialised, ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)


# ── Factory ──────────────────────────────────────────────────────────────────

def _make_cache() -> MemoryCache | RedisCache:
    if settings.REDIS_URL:
        logger.info("[Cache] Using Redis at %s", settings.REDIS_URL[:30] + "…")
        return RedisCache(settings.REDIS_URL)
    logger.info("[Cache] Using in-memory fallback (no REDIS_URL)")
    return MemoryCache()


# ── Singleton instances ──────────────────────────────────────────────────────

answer_cache = _make_cache()
ext_cache = _make_cache()


# ── Convenience helpers ──────────────────────────────────────────────────────

async def answer_cache_get(key: str) -> tuple | None:
    """Return (answer_text, citations, intent_str, rs) or None."""
    return await answer_cache.get(key)


async def answer_cache_set(
    key: str,
    answer_text: str,
    citations: list,
    intent_str: str,
    rs: str,
) -> None:
    await answer_cache.set(key, (answer_text, citations, intent_str, rs), ttl=ANSWER_TTL)


async def ext_cache_get(source: str, term: str) -> Any | None:
    key = f"{source}:{term.strip().lower()}"
    return await ext_cache.get(key)


async def ext_cache_set(source: str, term: str, data: Any) -> None:
    key = f"{source}:{term.strip().lower()}"
    ttl = EXT_TTLS.get(source, 3600)
    await ext_cache.set(key, data, ttl=ttl)
