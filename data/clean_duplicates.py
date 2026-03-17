"""
Clean RxBuddy PostgreSQL database of duplicate and low-quality questions.

Step 1: Remove exact duplicates (keep lowest id)
Step 2: Remove near-duplicates (TF-IDF cosine similarity >= 85%)
Step 3: Remove low-quality questions (too short, no letters, just drug names)
Step 4: Print full report
Step 5: VACUUM the database to free storage

Run: python data/clean_duplicates.py
Requires: DATABASE_URL in .env
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import MetaData, Table, bindparam, create_engine, select, text
from sqlalchemy.engine import Engine


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SIMILARITY_THRESHOLD = 0.85  # 85% similar = near-duplicate
MIN_QUESTION_LENGTH = 10
QUESTION_WORDS = frozenset(
    "what how can should is does when why who will would could may might do did am are was were".split()
)


def _database_url() -> str:
    """Load DATABASE_URL from .env and ensure psycopg driver."""
    load_dotenv()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Add it to your .env file, for example:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/rxbuddy"
        )
    if "://" in url and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _get_engine() -> Engine:
    """Create SQLAlchemy engine for PostgreSQL."""
    return create_engine(_database_url(), future=True, pool_pre_ping=True)


def step1_exact_duplicates(engine: Engine, metadata: MetaData) -> tuple[int, int]:
    """
    Step 1: Remove exact duplicates.
    For each group of identical question text, keep the one with the lowest id.
    Returns (ids_deleted, count_before).
    """
    questions = Table("questions", metadata, extend_existing=True, autoload_with=engine)

    with engine.connect() as conn:
        rows = conn.execute(select(questions.c.id, questions.c.question)).mappings().all()
    count_before = len(rows)

    # Group by question text (stripped; exact match)
    groups: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        q = (r["question"] or "").strip()
        groups[q].append(int(r["id"]))

    # For each group with duplicates, keep min(id), delete the rest
    ids_to_delete: set[int] = set()
    for q_text, ids in groups.items():
        if len(ids) > 1:
            keep = min(ids)
            for i in ids:
                if i != keep:
                    ids_to_delete.add(i)

    if not ids_to_delete:
        return 0, count_before

    # Nullify search_logs references, then delete
    ids_list = list(ids_to_delete)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE search_logs SET matched_question_id = NULL "
                "WHERE matched_question_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids_list},
        )
        conn.execute(questions.delete().where(questions.c.id.in_(ids_to_delete)))

    return len(ids_to_delete), count_before


def step2_near_duplicates(engine: Engine, metadata: MetaData) -> tuple[int, int]:
    """
    Step 2: Remove near-duplicates using TF-IDF cosine similarity.
    If two questions are >= 85% similar, keep the one with lower id.
    Returns (ids_deleted, count_before).
    """
    questions = Table("questions", metadata, extend_existing=True, autoload_with=engine)

    with engine.connect() as conn:
        rows = conn.execute(select(questions.c.id, questions.c.question)).mappings().all()
    count_before = len(rows)

    if count_before < 2:
        return 0, count_before

    ids = [int(r["id"]) for r in rows]
    texts = [(r["question"] or "").strip() for r in rows]

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
    )
    matrix = vectorizer.fit_transform(texts)
    sim = cosine_similarity(matrix, matrix)

    # For each pair (i,j) with sim >= threshold, keep lower id, delete higher id
    ids_to_delete: set[int] = set()
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= SIMILARITY_THRESHOLD:
                delete_id = max(ids[i], ids[j])  # Keep lower id
                ids_to_delete.add(delete_id)

    if not ids_to_delete:
        return 0, count_before

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE search_logs SET matched_question_id = NULL "
                "WHERE matched_question_id = ANY(:ids)"
            ),
            {"ids": list(ids_to_delete)},
        )
        conn.execute(questions.delete().where(questions.c.id.in_(ids_to_delete)))

    return len(ids_to_delete), count_before


def _is_low_quality(question: str) -> bool:
    """
    Returns True if the question should be deleted as low-quality.
    - Less than 10 characters
    - No letters at all
    - Just a drug name with no actual question (no question words, no ?, short)
    """
    q = (question or "").strip()
    if len(q) < MIN_QUESTION_LENGTH:
        return True
    if not re.search(r"[a-zA-Z]", q):
        return True
    # "Just drug names" = short, no question mark, no question words
    q_lower = q.lower()
    has_question_word = any(w in q_lower for w in QUESTION_WORDS)
    has_question_mark = "?" in q
    if len(q) < 40 and not has_question_word and not has_question_mark:
        return True
    return False


def step3_low_quality(engine: Engine, metadata: MetaData) -> tuple[int, int]:
    """
    Step 3: Remove low-quality questions.
    Returns (ids_deleted, count_before).
    """
    questions = Table("questions", metadata, extend_existing=True, autoload_with=engine)

    with engine.connect() as conn:
        rows = conn.execute(select(questions.c.id, questions.c.question)).mappings().all()
    count_before = len(rows)

    ids_to_delete: set[int] = set()
    for r in rows:
        if _is_low_quality(str(r["question"] or "")):
            ids_to_delete.add(int(r["id"]))

    if not ids_to_delete:
        return 0, count_before

    ids_list = list(ids_to_delete)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE search_logs SET matched_question_id = NULL "
                "WHERE matched_question_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids_list},
        )
        conn.execute(questions.delete().where(questions.c.id.in_(ids_to_delete)))

    return len(ids_to_delete), count_before


def _count_questions(engine: Engine) -> int:
    """Return total number of questions in the database."""
    with engine.connect() as conn:
        r = conn.execute(text("SELECT COUNT(*) FROM questions"))
        return int(r.scalar() or 0)


def step5_vacuum(engine: Engine) -> None:
    """
    Step 5: Run VACUUM to reclaim storage after deletions.
    VACUUM cannot run inside a transaction; we use autocommit.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM questions"))
    print("VACUUM completed.")


def main() -> None:
    load_dotenv()
    engine = _get_engine()
    metadata = MetaData()

    count_before_all = _count_questions(engine)
    print(f"Questions before cleaning: {count_before_all}")
    print()

    exact_removed = 0
    near_removed = 0
    low_quality_removed = 0

    # Step 1: Exact duplicates
    print("Step 1: Removing exact duplicates...")
    exact_removed, _ = step1_exact_duplicates(engine, metadata)
    print(f"  Exact duplicates removed: {exact_removed}")

    # Step 2: Near-duplicates
    print("Step 2: Removing near-duplicates (TF-IDF >= 85% similar)...")
    near_removed, _ = step2_near_duplicates(engine, metadata)
    print(f"  Near duplicates removed: {near_removed}")

    # Step 3: Low quality
    print("Step 3: Removing low-quality questions...")
    low_quality_removed, _ = step3_low_quality(engine, metadata)
    print(f"  Low quality removed: {low_quality_removed}")

    count_after = _count_questions(engine)
    total_removed = exact_removed + near_removed + low_quality_removed

    # Step 4: Report
    print()
    print("=" * 50)
    print("CLEANING REPORT")
    print("=" * 50)
    print(f"Exact duplicates removed: {exact_removed}")
    print(f"Near duplicates removed: {near_removed}")
    print(f"Low quality removed: {low_quality_removed}")
    print(f"Questions before cleaning: {count_before_all}")
    print(f"Questions after cleaning: {count_after}")
    print(f"Memory saved: {total_removed} rows")
    print("=" * 50)

    # Step 5: VACUUM
    if total_removed > 0:
        print()
        print("Step 5: Running VACUUM to free storage...")
        step5_vacuum(engine)
    else:
        print()
        print("No rows deleted. Skipping VACUUM.")

    print("Done.")


if __name__ == "__main__":
    main()
