from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors


@dataclass(frozen=True)
class Match:
    id: int
    question: str
    category: str | None
    tags: list[str]
    score: float


_CACHE: dict[str, object] = {}


def _project_root() -> Path:
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


def build_index(
    *,
    csv_path: str | Path | None = None,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> None:
    """
    Loads the CSV, builds sentence embeddings, and fits a KNN index.

    This uses `sentence-transformers`, which you will install later:
      pip install sentence-transformers
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

    # Import here so the rest of the project can run without this dependency.
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(model_name)
    texts = df["question"].astype(str).fillna("").tolist()
    embeddings = encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    embeddings = np.asarray(embeddings, dtype=np.float32)

    # Cosine similarity on normalized vectors is equivalent to dot product.
    knn = NearestNeighbors(n_neighbors=10, metric="cosine")
    knn.fit(embeddings)

    _CACHE["df"] = df
    _CACHE["encoder"] = encoder
    _CACHE["embeddings"] = embeddings
    _CACHE["knn"] = knn


def _ensure_index(**kwargs: object) -> None:
    if "knn" not in _CACHE or "encoder" not in _CACHE or "df" not in _CACHE:
        build_index(**kwargs)  # type: ignore[arg-type]


def search(
    query: str,
    *,
    top_k: int = 5,
    csv_path: str | Path | None = None,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> list[Match]:
    """
    Returns the top_k most similar questions using sentence embeddings + KNN.
    """
    q = (query or "").strip()
    if not q:
        return []

    _ensure_index(csv_path=csv_path, model_name=model_name)
    df: pd.DataFrame = _CACHE["df"]  # type: ignore[assignment]
    encoder = _CACHE["encoder"]  # SentenceTransformer
    knn: NearestNeighbors = _CACHE["knn"]  # type: ignore[assignment]

    q_emb = encoder.encode([q], normalize_embeddings=True, show_progress_bar=False)
    distances, indices = knn.kneighbors(
        np.asarray(q_emb, dtype=np.float32), n_neighbors=max(1, int(top_k))
    )
    idxs = indices.ravel().tolist()
    dists = distances.ravel().tolist()

    results: list[Match] = []
    for i, dist in zip(idxs, dists, strict=False):
        row = df.iloc[int(i)]
        # KNN returns a distance (smaller = closer). Convert to a 0..1 similarity score:
        # score = 1 / (1 + distance)
        score = 1.0 / (1.0 + float(dist))
        results.append(
            Match(
                id=int(row["id"]),
                question=str(row["question"]),
                category=(None if pd.isna(row["category"]) else str(row["category"])),
                tags=_split_tags(row.get("tags")),
                score=score,
            )
        )

    return results

