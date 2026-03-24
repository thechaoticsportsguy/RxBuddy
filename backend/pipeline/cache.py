"""
Pipeline Step 2 / 9 — PostgreSQL Answer Cache.

Simple cache backed by a dedicated PostgreSQL table.
Keyed by normalised query string. TTL = 7 days.

Cache hits return instantly (<200ms).
Cache misses fall through to the full pipeline.

Table schema (auto-created on first use):
  answer_cache (
    query_key   TEXT PRIMARY KEY,
    response    JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
  )
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("rxbuddy.pipeline.cache")

CACHE_TTL_DAYS = 7

# ── In-memory L1 cache (fast path — avoids DB round-trip) ────────────────────
# Keyed by normalised query string. Value: (response_dict, expires_at_timestamp)
_L1_CACHE: dict[str, tuple[dict, float]] = {}
_L1_MAX = 500


def _normalize_cache_key(query: str) -> str:
    """Normalise a query into a stable cache key."""
    key = query.strip().lower()
    key = re.sub(r"\s+", " ", key)
    key = re.sub(r"[^\w\s]", "", key)
    return key.strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── L1 (in-memory) ───────────────────────────────────────────────────────────

def cache_get(query: str) -> dict | None:
    """
    Check L1 in-memory cache first, then PostgreSQL.
    Returns the cached response dict or None.
    """
    key = _normalize_cache_key(query)
    if not key:
        return None

    # L1 check
    entry = _L1_CACHE.get(key)
    if entry:
        response, expires_ts = entry
        if _utc_now().timestamp() < expires_ts:
            logger.info("[Cache] L1 HIT for: %.60s", query)
            return response
        else:
            _L1_CACHE.pop(key, None)

    # L2 (PostgreSQL) check
    return _pg_cache_get(key)


def cache_set(query: str, response: dict) -> None:
    """Store a response in both L1 and PostgreSQL cache."""
    key = _normalize_cache_key(query)
    if not key:
        return

    expires_at = _utc_now() + timedelta(days=CACHE_TTL_DAYS)
    expires_ts = expires_at.timestamp()

    # L1 set (evict oldest if full)
    if len(_L1_CACHE) >= _L1_MAX:
        oldest = list(_L1_CACHE.keys())[:100]
        for k in oldest:
            _L1_CACHE.pop(k, None)
    _L1_CACHE[key] = (response, expires_ts)

    # L2 (PostgreSQL) set
    _pg_cache_set(key, response, expires_at)


# ── L2 (PostgreSQL) ──────────────────────────────────────────────────────────

def _get_engine():
    """Get the SQLAlchemy engine. Import lazily to avoid circular deps."""
    try:
        # Try importing from the already-initialised main module
        # This avoids creating a second engine
        from sqlalchemy import create_engine
        url = os.getenv("DATABASE_URL", "")
        if not url:
            return None
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return create_engine(url, future=True, pool_pre_ping=True)
    except Exception:
        return None


def _ensure_table(engine) -> bool:
    """Create the answer_cache table if it doesn't exist."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS answer_cache (
                    query_key  TEXT PRIMARY KEY,
                    response   JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """))
        return True
    except Exception as exc:
        logger.debug("[Cache] Could not create answer_cache table: %s", exc)
        return False


def _pg_cache_get(key: str) -> dict | None:
    """Look up a cached response in PostgreSQL."""
    engine = _get_engine()
    if not engine:
        return None
    try:
        _ensure_table(engine)
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT response, expires_at FROM answer_cache WHERE query_key = :key"),
                {"key": key},
            ).mappings().first()

        if not row:
            return None

        expires_at = row["expires_at"]
        if expires_at and expires_at.replace(tzinfo=timezone.utc) < _utc_now():
            # Expired — delete and return None
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM answer_cache WHERE query_key = :key"), {"key": key})
            except Exception:
                pass
            return None

        response = row["response"]
        if isinstance(response, str):
            response = json.loads(response)

        logger.info("[Cache] L2 (PostgreSQL) HIT for key: %.60s", key)

        # Promote to L1
        _L1_CACHE[key] = (response, expires_at.replace(tzinfo=timezone.utc).timestamp())

        return response

    except Exception as exc:
        logger.debug("[Cache] PostgreSQL GET failed: %s", exc)
        return None


def _pg_cache_set(key: str, response: dict, expires_at: datetime) -> None:
    """Store a response in PostgreSQL cache."""
    engine = _get_engine()
    if not engine:
        return
    try:
        _ensure_table(engine)
        from sqlalchemy import text
        response_json = json.dumps(response, default=str)
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO answer_cache (query_key, response, created_at, expires_at)
                    VALUES (:key, :response::jsonb, :created_at, :expires_at)
                    ON CONFLICT (query_key) DO UPDATE
                    SET response = :response::jsonb,
                        created_at = :created_at,
                        expires_at = :expires_at
                """),
                {
                    "key": key,
                    "response": response_json,
                    "created_at": _utc_now(),
                    "expires_at": expires_at,
                },
            )
        logger.info("[Cache] L2 (PostgreSQL) SET for key: %.60s", key)
    except Exception as exc:
        logger.debug("[Cache] PostgreSQL SET failed: %s", exc)
