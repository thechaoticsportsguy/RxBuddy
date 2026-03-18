from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, Text, create_engine, select, update
from sqlalchemy.dialects.postgresql import VARCHAR
from sqlalchemy.sql import and_, or_


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Add it to your .env file, for example:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/rxbuddy"
        )
    if "://" in url and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is missing. Add it to your .env file or Railway variables."
        )
    return key


def _make_prompt(question: str) -> str:
    """
    Create a prompt using the intent-classified medication answering engine.

    Output format:
    Answer: YES / NO / USUALLY YES / NEEDS REVIEW
    Why: 1-2 simple sentences
    Important notes: bullet list
    Get medical help now if: bullet list
    """
    return f"""You are a medication question answering engine.

Your job is to answer the USER'S EXACT QUESTION only.
Do not answer a related question.
Do not answer based on one keyword alone.
Do not switch topics.
Do not confuse:
- drug interaction questions
- overdose questions
- side effect questions
- missed dose questions
- allergy questions
- pregnancy questions
- food/alcohol questions
- emergency symptom questions

ABSOLUTE RULE:
Before answering, you must classify the user's question into exactly one primary intent category.

VALID INTENT CATEGORIES:
1. Drug interaction / compatibility
2. Overdose / poisoning
3. Side effects
4. Missed dose
5. How to take / timing
6. Contraindication / when not to use
7. Food / alcohol interaction
8. Pregnancy / breastfeeding
9. Storage
10. General drug information
11. Emergency symptom triage
12. Unknown / ambiguous

STEP 1 — READ THE FULL QUESTION
STEP 2 — EXTRACT THE CORE ASK
STEP 3 — INTENT CHECK
STEP 4 — ANSWER THE EXACT QUESTION FIRST
STEP 5 — SAFETY FILTER
STEP 6 — CROSS-EXAMINATION CHECK
STEP 7 — CONTRADICTION BLOCK
STEP 8 — SIMPLICITY RULE

Output format EXACTLY:
Answer: [YES / NO / USUALLY YES / NEEDS REVIEW]
Why: [1-2 simple sentences]
Important notes: [only relevant bullets]
Get medical help now if: [only truly relevant urgent symptoms]

STEP 9 — DO NOT HALLUCINATE
STEP 10 — STRICT MISMATCH PREVENTION
STEP 11 — FINAL SELF-AUDIT

PATIENT QUESTION: {question}"""


def _generate_answer(question: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=_anthropic_key())
    prompt = _make_prompt(question)

    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    text = (resp.content[0].text or "").strip()
    if not text:
        raise RuntimeError("Claude returned an empty response.")
    return text


def main() -> None:
    """
    Generates and backfills AI answers for questions missing an answer.

    What it does:
    1) Loads DATABASE_URL + ANTHROPIC_API_KEY from .env
    2) Fetches questions where answer is NULL or empty
    3) Calls Claude (cheap/fast model) to generate an answer
    4) Saves the answer back to questions.answer
    5) Prints progress and sleeps 0.5s between calls
    """

    load_dotenv()

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    metadata = MetaData()

    # Minimal table mapping (only columns we need).
    questions = Table(
        "questions",
        metadata,
        # columns must match existing DB schema
        # (we only define the ones we read/write here)
        # id is enough for WHERE, question for prompt, answer for update
        # category/tags aren't needed for this script
        # types here are fine even if DB is already created
        # autoload isn't required for simple updates
        # NOTE: don't call create_all in this script
        extend_existing=True,
    )
    from sqlalchemy import Column, Integer

    questions.append_column(Column("id", Integer, primary_key=True))
    questions.append_column(Column("question", Text, nullable=False))
    questions.append_column(Column("answer", Text, nullable=True))
    questions.append_column(Column("category", VARCHAR(50), nullable=True))

    missing_answer_filter = or_(
        questions.c.answer.is_(None),
        questions.c.answer == "",
        and_(questions.c.answer.is_not(None), questions.c.answer.op("~")(r"^\s+$")),
    )

    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(questions.c.id, questions.c.question).where(missing_answer_filter).order_by(questions.c.id)
            )
            .mappings()
            .all()
        )

    total = len(rows)
    if total == 0:
        print("All questions already have answers. Nothing to do.")
        return

    for i, r in enumerate(rows, start=1):
        qid = int(r["id"])
        question = str(r["question"])

        try:
            answer = _generate_answer(question)
        except Exception as e:
            print(f"ERROR generating answer for id={qid}: {e}")
            # skip and continue so one failure doesn't stop the whole job
            continue

        # Save answer to DB
        with engine.begin() as conn:
            conn.execute(
                update(questions).where(questions.c.id == qid).values(answer=answer)
            )

        preview = question if len(question) <= 60 else (question[:57] + "...")
        print(f"Answered {i}/{total}: {preview}")

        time.sleep(0.5)

    print("Done. Answers generated and saved.")


if __name__ == "__main__":
    main()

