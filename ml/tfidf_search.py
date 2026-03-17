from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass(frozen=True)
class Match:
    id: int
    question: str
    category: str | None
    tags: list[str]
    score: float


_CACHE: dict[str, object] = {}


def _project_root() -> Path:
    # ml/ -> project root
    return Path(__file__).resolve().parents[1]


def _default_csv_path() -> Path:
    return _project_root() / "data" / "questions_cleaned.csv"


def _split_tags(tag_str: object) -> list[str]:
    if tag_str is None:
        return []
    s = str(tag_str).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(";") if p.strip()]


def build_index(csv_path: str | Path | None = None) -> None:
    """
    Loads questions_cleaned.csv and builds the TF-IDF search index.

    We cache the result in memory so it doesn’t rebuild on every request.
    """
    path = Path(csv_path) if csv_path is not None else _default_csv_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run `python data/load_data.py` to generate questions_cleaned.csv."
        )

    df = pd.read_csv(path)
    for col in ["id", "question", "category", "tags"]:
        if col not in df.columns:
            raise ValueError(f"CSV is missing required column: {col}")

    # TF-IDF works on text. We use the question text (lowercased).
    texts = df["question"].astype(str).fillna("").tolist()

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
    )
    matrix = vectorizer.fit_transform(texts)  # shape: (N, vocab)

    _CACHE["df"] = df
    _CACHE["vectorizer"] = vectorizer
    _CACHE["matrix"] = matrix


def _ensure_index(csv_path: str | Path | None = None) -> None:
    if "vectorizer" not in _CACHE or "matrix" not in _CACHE or "df" not in _CACHE:
        build_index(csv_path=csv_path)


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
                category=(None if pd.isna(row["category"]) else str(row["category"])),
                tags=_split_tags(row.get("tags")),
                score=float(sims[int(i)]),
            )
        )

    return results

