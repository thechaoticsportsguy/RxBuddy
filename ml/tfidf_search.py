from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    id: int
    question: str
    category: str | None
    tags: list[str]
    score: float
    answer: str | None = None  # Include answer for exact match returns


_CACHE: dict[str, object] = {}


def _project_root() -> Path:
    # ml/ -> project root
    return Path(__file__).resolve().parents[1]


def _default_csv_path() -> Path:
    return _project_root() / "data" / "questions_cleaned.csv"


def _split_tags(tag_str: object) -> list[str]:
    if tag_str is None:
        return []
    if isinstance(tag_str, list):
        return [str(t) for t in tag_str]
    s = str(tag_str).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(";") if p.strip()]


def _get_database_url() -> str | None:
    """Get DATABASE_URL from environment if available."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return None
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_index_from_db() -> bool:
    """
    Build TF-IDF index directly from PostgreSQL database.
    This ensures the index includes newly added questions from self-learning.
    
    Returns True if successful, False if database not available.
    """
    db_url = _get_database_url()
    if not db_url:
        logger.warning("[TF-IDF] DATABASE_URL not set, cannot build from DB")
        return False
    
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(db_url, future=True, pool_pre_ping=True)
        
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT id, question, category, tags, answer FROM questions ORDER BY id"
            ))
            rows = result.mappings().all()
        
        if not rows:
            logger.warning("[TF-IDF] No questions found in database")
            return False
        
        # Convert to DataFrame
        data = []
        for r in rows:
            tags_val = r.get("tags")
            if isinstance(tags_val, list):
                tags_str = ";".join(str(t) for t in tags_val)
            else:
                tags_str = str(tags_val) if tags_val else ""
            
            data.append({
                "id": int(r["id"]),
                "question": str(r["question"]),
                "category": r.get("category"),
                "tags": tags_str,
                "answer": r.get("answer"),
            })
        
        df = pd.DataFrame(data)
        
        # Build TF-IDF index
        texts = df["question"].astype(str).fillna("").tolist()
        
        vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words="english",
            ngram_range=(1, 2),
            min_df=1,
        )
        matrix = vectorizer.fit_transform(texts)
        
        _CACHE["df"] = df
        _CACHE["vectorizer"] = vectorizer
        _CACHE["matrix"] = matrix
        _CACHE["source"] = "database"
        
        logger.info("[TF-IDF] Built index from database with %d questions", len(df))
        return True
        
    except Exception as e:
        logger.error("[TF-IDF] Failed to build index from database: %s", e)
        return False


def build_index(csv_path: str | Path | None = None) -> None:
    """
    Loads questions and builds the TF-IDF search index.
    
    Tries database first (to include self-learned questions),
    falls back to CSV file if database not available.
    """
    # Try database first
    if build_index_from_db():
        return
    
    # Fall back to CSV file
    path = Path(csv_path) if csv_path is not None else _default_csv_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run `python data/load_data.py` to generate questions_cleaned.csv."
        )

    df = pd.read_csv(path)
    for col in ["id", "question", "category", "tags"]:
        if col not in df.columns:
            raise ValueError(f"CSV is missing required column: {col}")

    texts = df["question"].astype(str).fillna("").tolist()

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
    )
    matrix = vectorizer.fit_transform(texts)

    _CACHE["df"] = df
    _CACHE["vectorizer"] = vectorizer
    _CACHE["matrix"] = matrix
    _CACHE["source"] = "csv"
    
    logger.info("[TF-IDF] Built index from CSV with %d questions", len(df))


def rebuild_index() -> None:
    """
    Force rebuild the TF-IDF index.
    Call this after adding new questions to the database.
    """
    logger.info("[TF-IDF] Rebuilding index...")
    _CACHE.clear()
    build_index()


def _ensure_index(csv_path: str | Path | None = None) -> None:
    if "vectorizer" not in _CACHE or "matrix" not in _CACHE or "df" not in _CACHE:
        build_index(csv_path=csv_path)


def find_exact_match(query: str) -> Match | None:
    """
    Check for exact or near-exact match in the database.
    Returns a Match if found (case-insensitive), otherwise None.
    
    This is called BEFORE TF-IDF to instantly return cached answers.
    """
    _ensure_index()
    df: pd.DataFrame = _CACHE["df"]  # type: ignore
    
    query_lower = (query or "").strip().lower()
    if not query_lower:
        return None
    
    # Check for exact match (case-insensitive)
    for idx, row in df.iterrows():
        q = str(row["question"]).strip().lower()
        if q == query_lower:
            logger.info("[TF-IDF] Exact match found for: %.60s", query)
            return Match(
                id=int(row["id"]),
                question=str(row["question"]),
                category=(None if pd.isna(row.get("category")) else str(row["category"])),
                tags=_split_tags(row.get("tags")),
                score=1.0,
                answer=str(row["answer"]) if row.get("answer") and not pd.isna(row.get("answer")) else None,
            )
    
    return None


def search(query: str, *, top_k: int = 5, csv_path: str | Path | None = None) -> list[Match]:
    """
    Given a user query, returns the top_k most similar questions + similarity score.

    Score is cosine similarity from 0.0 to 1.0 (higher is more similar).
    """
    q = (query or "").strip()
    if not q:
        return []

    _ensure_index(csv_path=csv_path)
    df: pd.DataFrame = _CACHE["df"]  # type: ignore[assignment]
    vectorizer: TfidfVectorizer = _CACHE["vectorizer"]  # type: ignore[assignment]
    matrix = _CACHE["matrix"]  # scipy sparse matrix

    q_vec = vectorizer.transform([q])
    sims = cosine_similarity(q_vec, matrix).ravel()  # shape: (N,)

    if sims.size == 0:
        return []

    k = max(1, int(top_k))
    top_idx = np.argsort(-sims)[:k]

    results: list[Match] = []
    for i in top_idx:
        row = df.iloc[int(i)]
        results.append(
            Match(
                id=int(row["id"]),
                question=str(row["question"]),
                category=(None if pd.isna(row.get("category")) else str(row["category"])),
                tags=_split_tags(row.get("tags")),
                score=float(sims[int(i)]),
                answer=str(row["answer"]) if row.get("answer") and not pd.isna(row.get("answer")) else None,
            )
        )

    return results
