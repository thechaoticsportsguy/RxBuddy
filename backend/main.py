from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, MetaData, Table, Text, create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, VARCHAR


# ---------- 1) Load secrets from .env ----------
# `.env` lives in your project root and is NOT committed to GitHub.
load_dotenv()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _database_url() -> str:
    """
    Reads DATABASE_URL from the environment.

    We also force the 'psycopg' driver (postgresql+psycopg://...) because this project
    installs `psycopg[binary]` (not psycopg2).
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Create a .env file in the project root with:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:6767/rxbuddy"
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# ---------- 2) Connect to PostgreSQL with SQLAlchemy ----------
engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
metadata = MetaData()

# We define the tables in code so we can query/insert easily.
# (The tables are created by `data/seed_db.py`.)
questions_table = Table(
    "questions",
    metadata,
    # These Column definitions match what we created earlier:
    # id, question, category, tags, answer, created_at
    # NOTE: We don't call create_all() here; we just map the table.
    # If the table doesn't exist in Postgres, seeding needs to run first.
    #
    # SQLAlchemy needs column objects; Table(..., autoload_with=engine) would also work,
    # but we keep it explicit for beginners.
    #
    # (Using dialect types is okay with Postgres.)
    #
    # Primary key:
    # - id: integer
    #
    # Data:
    # - question: text
    # - category: varchar(50)
    # - tags: text[]
    # - answer: text (nullable for now)
    # - created_at: timestamptz
    #
    # We set extend_existing=True so it won’t crash if imported twice.
    # (Example: autoreload in development.)
    #
    # IMPORTANT: These definitions do not change your DB schema by themselves.
    #
    # Columns:
    # pylint: disable=too-many-function-args
    # (Cursor/linters may not be present; this is fine.)
    #
    # Use SQLAlchemy core columns:
    # (We import types above; columns are created implicitly by Column objects.)
    #
    # We’ll declare columns via sqlalchemy.Column to be explicit.
    extend_existing=True,
)

search_logs_table = Table(
    "search_logs",
    metadata,
    extend_existing=True,
)

# Define columns (explicitly) after table creation to keep imports minimal.
from sqlalchemy import Column, ForeignKey

questions_table.append_column(Column("id", Integer, primary_key=True))
questions_table.append_column(Column("question", Text, nullable=False))
questions_table.append_column(Column("category", VARCHAR(50), nullable=True))
questions_table.append_column(Column("tags", ARRAY(Text), nullable=True))
questions_table.append_column(Column("answer", Text, nullable=True))
questions_table.append_column(Column("created_at", DateTime(timezone=True), nullable=True))

search_logs_table.append_column(Column("id", Integer, primary_key=True))
search_logs_table.append_column(Column("query", Text, nullable=False))
search_logs_table.append_column(
    Column("matched_question_id", Integer, ForeignKey("questions.id"), nullable=True)
)
search_logs_table.append_column(Column("clicked", Boolean, nullable=False, default=False))
search_logs_table.append_column(Column("session_id", VARCHAR(100), nullable=True))
search_logs_table.append_column(Column("searched_at", DateTime(timezone=True), nullable=False))


# ---------- 3) FastAPI app ----------
app = FastAPI(title="RxBuddy API", version="0.1.0")


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "RxBuddy is live"}


# ---------- 4) Request/response models ----------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="What the user typed or spoke")


class QuestionMatch(BaseModel):
    id: int
    question: str
    category: Optional[str] = None
    tags: list[str] = []


class SearchResponse(BaseModel):
    query: str
    results: list[QuestionMatch]


class LogRequest(BaseModel):
    query: str = Field(..., min_length=1)
    matched_question_id: Optional[int] = None
    clicked: bool = False
    session_id: Optional[str] = None


class LogResponse(BaseModel):
    ok: bool
    log_id: int


# ---------- 5) Simple search logic ----------
_word_re = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _word_re.findall(text or "") if len(t) >= 2]


def _score(query: str, candidate_question: str) -> float:
    """
    Very simple scoring:
    - Token overlap (how many words in common)
    - Bonus if query is a substring of the candidate
    """
    q = query.strip().lower()
    c = (candidate_question or "").strip().lower()
    if not c:
        return 0.0

    qt = set(_tokens(q))
    ct = set(_tokens(c))
    overlap = len(qt & ct)
    score = float(overlap)

    if q and q in c:
        score += 2.0

    # Tiny length normalization so very long questions don’t dominate.
    score += min(len(q), 60) / 100.0
    return score


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    """
    Takes a user query and returns the top 5 matching questions.

    How it works (simple version):
    1) Pull a small set of candidate rows from Postgres using ILIKE on tokens
    2) Score candidates in Python
    3) Return the best 5
    """
    user_query = req.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    toks = _tokens(user_query)
    if not toks:
        raise HTTPException(status_code=400, detail="Query must contain letters or numbers.")

    # Build an OR filter like:
    # question ILIKE %token1% OR question ILIKE %token2% ...
    # This keeps it beginner-friendly and works for our 532-row dataset.
    conditions = []
    for t in toks[:8]:  # limit tokens so we don’t build a huge query
        conditions.append(questions_table.c.question.ilike(f"%{t}%"))

    stmt = select(
        questions_table.c.id,
        questions_table.c.question,
        questions_table.c.category,
        questions_table.c.tags,
    )
    for cond in conditions:
        stmt = stmt.where(cond) if stmt._where_criteria == () else stmt.where(cond)  # type: ignore[attr-defined]

    # The above would AND conditions; we want OR. So do it properly:
    from sqlalchemy import or_

    stmt = select(
        questions_table.c.id,
        questions_table.c.question,
        questions_table.c.category,
        questions_table.c.tags,
    ).where(or_(*conditions))

    # Grab a limited candidate set; we’ll rank in Python.
    stmt = stmt.limit(200)

    try:
        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
    except Exception as e:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail=f"Database error while searching. Did you run `python data/seed_db.py`? ({e})",
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        s = _score(user_query, r["question"])
        scored.append((s, dict(r)))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [item for item in scored if item[0] > 0][:5]

    results: list[QuestionMatch] = []
    for _, r in top:
        tags = r.get("tags") or []
        results.append(
            QuestionMatch(
                id=int(r["id"]),
                question=str(r["question"]),
                category=r.get("category"),
                tags=[str(t) for t in tags],
            )
        )

    return SearchResponse(query=user_query, results=results)


@app.post("/log", response_model=LogResponse)
def log_search(req: LogRequest) -> LogResponse:
    """
    Logs a search event to `search_logs`.

    Your frontend can call this after /search (or whenever the user clicks a result).
    """
    q = req.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    row = {
        "query": q,
        "matched_question_id": req.matched_question_id,
        "clicked": bool(req.clicked),
        "session_id": req.session_id,
        "searched_at": _utc_now(),
    }

    try:
        with engine.begin() as conn:
            result = conn.execute(search_logs_table.insert().returning(search_logs_table.c.id), row)
            log_id = int(result.scalar_one())
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Database error while logging. ({e})")

    return LogResponse(ok=True, log_id=log_id)

