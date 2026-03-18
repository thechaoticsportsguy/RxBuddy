"""
Clear generic placeholder answers from the RxBuddy PostgreSQL database.

Finds questions where the answer contains any of these generic phrases:
- "Follow the package directions"
- "Use the lowest effective dose"
- "Symptoms are severe or getting worse"
- "Avoid taking more than the max daily dose"
- "If you're unsure, ask your pharmacist"

Sets answer = NULL for those questions so they can be regenerated with specific answers.

Run: python data/fix_wrong_answers.py
Requires: DATABASE_URL in .env
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# Phrases that indicate a generic placeholder answer (case-insensitive match)
GENERIC_PHRASES = [
    "Follow the package directions",
    "Use the lowest effective dose",
    "Symptoms are severe or getting worse",
    "Avoid taking more than the max daily dose",
    "If you're unsure, ask your pharmacist",
]


def _database_url() -> str:
    """Load DATABASE_URL from environment and ensure psycopg driver."""
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


def main() -> None:
    load_dotenv()

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)

    # Build parameterized SQL: find questions where answer contains ANY generic phrase
    # Use ILIKE for case-insensitive matching; bind params prevent SQL injection
    conditions = " OR ".join(
        f"answer ILIKE :p{i}" for i in range(len(GENERIC_PHRASES))
    )
    params = {f"p{i}": f"%{phrase}%" for i, phrase in enumerate(GENERIC_PHRASES)}

    # First, count how many will be affected
    count_sql = text(
        f"SELECT COUNT(*) FROM questions WHERE answer IS NOT NULL AND ({conditions})"
    )

    with engine.connect() as conn:
        count = conn.execute(count_sql, params).scalar()
        count = int(count) if count is not None else 0

    if count == 0:
        print("No generic placeholder answers found. Nothing to clear.")
        return

    # Clear the answers
    update_sql = text(
        f"UPDATE questions SET answer = NULL WHERE answer IS NOT NULL AND ({conditions})"
    )

    with engine.begin() as conn:
        result = conn.execute(update_sql, params)

    # result.rowcount gives the number of rows updated
    cleared = result.rowcount if result.rowcount is not None else count

    print(f"Cleared {cleared} generic placeholder answer(s).")
    print("Run python data/generate_answers.py to regenerate specific answers for these questions.")


if __name__ == "__main__":
    main()
