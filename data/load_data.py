from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd


REQUIRED_COLUMNS = ["id", "question", "category", "tags"]


@dataclass(frozen=True)
class LoadResult:
    df: pd.DataFrame
    input_path: Path
    output_path: Optional[Path]


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u00a0", " ")
    return " ".join(text.split()).strip()


def _parse_tags(tag_str: str) -> List[str]:
    cleaned = _normalize_text(tag_str)
    if not cleaned:
        return []
    parts = [p.strip().lower() for p in cleaned.split(";")]
    parts = [p for p in parts if p]
    seen = set()
    unique: List[str] = []
    for t in parts:
        if t not in seen:
            unique.append(t)
            seen.add(t)
    return unique


def load_questions_csv(
    csv_path: str | Path,
    *,
    write_clean_csv: bool = True,
    clean_csv_path: str | Path | None = None,
) -> LoadResult:
    """
    Beginner-friendly loader for RxBuddy questions.

    What it does:
    - Reads `questions.csv`
    - Cleans up whitespace and obvious issues
    - Validates required columns + unique IDs
    - Optionally writes a cleaned CSV you can safely use elsewhere
    """

    input_path = Path(csv_path)
    if not input_path.exists():
        raise FileNotFoundError(f"CSV not found: {input_path}")

    df = pd.read_csv(input_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. Found columns: {list(df.columns)}"
        )

    # Keep only the columns we expect (prevents surprises later).
    df = df[REQUIRED_COLUMNS].copy()

    # Normalize text columns.
    df["question"] = df["question"].map(_normalize_text)
    df["category"] = df["category"].map(_normalize_text)
    df["tags"] = df["tags"].map(_normalize_text)

    # Drop empty questions (not useful for search).
    df = df[df["question"].astype(str).str.len() > 0].copy()

    # Convert id to integer and ensure it exists.
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    if df["id"].isna().any():
        bad_rows = df[df["id"].isna()]
        raise ValueError(
            "Some rows have a missing/invalid id. Fix these rows:\n"
            + bad_rows.to_string(index=False)
        )
    df["id"] = df["id"].astype(int)

    # Remove exact duplicates.
    df = df.drop_duplicates(subset=["question", "category", "tags"], keep="first")

    # Make sure IDs are unique.
    if df["id"].duplicated().any():
        dupes = df[df["id"].duplicated(keep=False)].sort_values("id")
        raise ValueError(
            "Duplicate ids found. Each row must have a unique id:\n"
            + dupes.to_string(index=False)
        )

    # Parse tags into a list column (handy for Python/ML code).
    df["tags_list"] = df["tags"].map(_parse_tags)

    # Helpful derived column for search/indexing.
    df["question_lower"] = df["question"].str.lower()

    output_path: Optional[Path] = None
    if write_clean_csv:
        if clean_csv_path is None:
            output_path = input_path.with_name("questions_cleaned.csv")
        else:
            output_path = Path(clean_csv_path)
        df.to_csv(output_path, index=False)

    return LoadResult(df=df, input_path=input_path, output_path=output_path)


def main() -> None:
    here = Path(__file__).resolve().parent
    input_csv = here / "questions.csv"
    result = load_questions_csv(input_csv, write_clean_csv=True)

    df = result.df
    print("Loaded questions.")
    print(f"- Input:  {result.input_path}")
    if result.output_path is not None:
        print(f"- Output: {result.output_path}")
    print(f"- Rows:   {len(df)}")
    print()
    print("Category counts:")
    print(df["category"].value_counts().to_string())


if __name__ == "__main__":
    main()

