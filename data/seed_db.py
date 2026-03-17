from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import VARCHAR


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    """
    Beginner-friendly database seeder for RxBuddy.

    What this script does:
    1) Loads DATABASE_URL from a `.env` file
    2) Connects to Postgres using SQLAlchemy
    3) Creates the `questions` and `search_logs` tables (if missing)
    4) Reads `data/questions_cleaned.csv`
    5) Inserts all rows into `questions`
    """

    # 1) Load environment variables from a local `.env` file.
    # This lets you keep secrets (like passwords) OUT of your code.
    load_dotenv()

    # 2) Read the connection string.
    # Example: postgresql://postgres:password@localhost:5432/rxbuddy
    import os

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing.\n"
            "Create a `.env` file in the project root (same folder as README.md) and add:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/rxbuddy"
        )

    # 3) Connect to Postgres.
    # Force SQLAlchemy to use the modern `psycopg` driver (we install `psycopg[binary]`).
    # If your URL is like: postgresql://user:pass@host:port/db
    # we convert it to:   postgresql+psycopg://user:pass@host:port/db
    if "://" in database_url and "+psycopg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    engine = create_engine(database_url, future=True)

    # 4) Define tables (SQLAlchemy uses these definitions to create tables).
    metadata = MetaData()
    questions = Table(
        "questions",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("question", Text, nullable=False),
        Column("category", VARCHAR(50), nullable=True),
        Column("tags", ARRAY(Text), nullable=True),
        Column("answer", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False, default=_utc_now),
    )

    search_logs = Table(
        "search_logs",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("query", Text, nullable=False),
        Column("matched_question_id", Integer, ForeignKey("questions.id"), nullable=True),
        Column("clicked", Boolean, nullable=False, server_default=text("false")),
        Column("session_id", VARCHAR(100), nullable=True),
        Column(
            "searched_at",
            DateTime(timezone=True),
            nullable=False,
            default=_utc_now,
        ),
    )

    # 5) Create tables if they don't exist.
    metadata.create_all(engine)

    # 6) Read the cleaned CSV.
    # We load from `questions_cleaned.csv` because it has already been cleaned/validated.
    here = Path(__file__).resolve().parent
    csv_path = here / "questions_cleaned.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Missing {csv_path}. Run `python data/load_data.py` first."
        )

    df = pd.read_csv(csv_path)

    # The cleaned CSV has extra columns like `tags_list` and `question_lower`.
    # We only need the columns that match the `questions` table.
    needed = ["id", "question", "category", "tags"]
    for col in needed:
        if col not in df.columns:
            raise ValueError(f"CSV is missing required column: {col}")
    df = df[needed].copy()

    # Convert tags "a;b;c" -> ["a","b","c"] to match Postgres TEXT[].
    def split_tags(tag_str: object) -> list[str]:
        if tag_str is None:
            return []
        s = str(tag_str).strip()
        if not s:
            return []
        return [p.strip() for p in s.split(";") if p.strip()]

    df["tags"] = df["tags"].map(split_tags)

    rows = df.to_dict(orient="records")

    # 7) Insert into the database.
    # We clear the table first so re-running this script doesn't create duplicates.
    with engine.begin() as conn:
        # Postgres requires truncating FK-related tables together.
        conn.execute(text("TRUNCATE TABLE search_logs, questions RESTART IDENTITY"))
        conn.execute(questions.insert(), rows)

    print("Seed complete.")
    print(f"- Inserted questions: {len(rows)}")
    print("- Tables ensured: questions, search_logs")


if __name__ == "__main__":
    main()

