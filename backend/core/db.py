"""Async (asyncpg) and sync (psycopg) SQLAlchemy engines + table definitions."""

from __future__ import annotations

import logging

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine as _create_sync_engine,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

logger = logging.getLogger("rxbuddy.db")

# ── Engines ──────────────────────────────────────────────────────────────────

async_engine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine — required by ML modules (tfidf_search, knn_search) and
# legacy pipeline code that hasn't been converted to async yet.
sync_engine = _create_sync_engine(
    settings.sync_database_url,
    future=True,
    pool_pre_ping=True,
)

# ── Table definitions ────────────────────────────────────────────────────────

metadata = MetaData()

questions_table = Table(
    "questions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("question", Text, nullable=False),
    Column("category", String(50), nullable=True),
    Column("tags", ARRAY(Text), nullable=True),
    Column("answer", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
    extend_existing=True,
)

search_logs_table = Table(
    "search_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("query", Text, nullable=False),
    Column("matched_question_id", Integer, ForeignKey("questions.id"), nullable=True),
    Column("clicked", Boolean, nullable=False, default=False),
    Column("session_id", String(100), nullable=True),
    Column("searched_at", DateTime(timezone=True), nullable=False),
    extend_existing=True,
)

drug_chat_cache = Table(
    "drug_chat_cache",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("drug_name", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("created_at", DateTime, server_default="now()"),
    extend_existing=True,
)


async def ensure_tables() -> None:
    """Create tables that don't exist yet (best-effort, non-blocking)."""
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(drug_chat_cache.create, checkfirst=True)
        logger.info("[DB] drug_chat_cache table ready")
    except Exception as exc:
        logger.warning("[DB] Could not create drug_chat_cache: %s", exc)
