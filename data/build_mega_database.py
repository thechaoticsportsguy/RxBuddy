"""
Build RxBuddy mega database (6,000+ questions) using free APIs.

Step 1: Fetch top 300 drugs from OpenFDA (drugs with most labels) or RxNorm fallback
Step 2: For each drug, fetch FDA label from OpenFDA
Step 3: For each drug, fetch DailyMed SPL metadata (optional)
Step 4: Generate 20 questions per drug from FDA data
Step 5: Save to data/mega_questions.csv and seed into PostgreSQL

Run: python data/build_mega_database.py
Requires: DATABASE_URL in .env for PostgreSQL seeding

For quick testing, set MAX_DRUGS = 5 at the top of this file.
"""

from __future__ import annotations

import csv
import os
import re
import time
from pathlib import Path
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_DRUGS = 300  # Limit: stop after processing this many drugs
RXNORM_URL = "https://rxnav.nlm.nih.gov/REST/allconcepts.json"
OPENFDA_URL = "https://api.fda.gov/drug/label.json"
DAILYMED_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
QUESTIONS_PER_DRUG = 20
DELAY_SECONDS = 0.5
USER_AGENT = "RxBuddy/1.0 (educational project; contact@example.com)"

# ---------------------------------------------------------------------------
# Step 1: Fetch drug names (OpenFDA count = drugs with labels, or RxNorm fallback)
# ---------------------------------------------------------------------------


def fetch_drug_names(limit: int = MAX_DRUGS) -> list[str]:
    """
    Get drug names from OpenFDA count endpoint (drugs with most labels first).
    These are guaranteed to have FDA labels. Falls back to RxNorm if OpenFDA fails.
    """
    # OpenFDA: get top drugs by label count (guaranteed to have labels)
    try:
        params = {"count": "openfda.generic_name.exact", "limit": limit}
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(OPENFDA_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        drugs = []
        seen = set()
        for r in results:
            name = (r.get("term") or "").strip()
            if name and name not in seen:
                seen.add(name)
                drugs.append(name)
            if len(drugs) >= limit:
                break
        if drugs:
            return drugs
    except Exception as e:
        print(f"  OpenFDA count failed ({e}), trying RxNorm...")

    # Fallback: RxNorm ingredients
    params = {"tty": "IN"}
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(RXNORM_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    group = data.get("minConceptGroup") or data.get("rxnormdata", {}).get("minConceptGroup", {})
    concepts = group.get("minConcept", [])
    if not isinstance(concepts, list):
        concepts = [concepts] if concepts else []
    drugs = []
    for c in concepts:
        name = (c.get("name") or "").strip()
        if name and name not in drugs:
            drugs.append(name)
        if len(drugs) >= limit:
            break
    return drugs


# ---------------------------------------------------------------------------
# Step 2: Fetch FDA label from OpenFDA
# ---------------------------------------------------------------------------


def _extract_text(obj: object) -> str:
    """Extract plain text from FDA label field (can be str or list of str)."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        return " ".join(str(x).strip() for x in obj if x).strip()
    return str(obj).strip()


def fetch_openfda_label(drug_name: str) -> dict[str, str] | None:
    """
    Fetch one FDA label for a drug. Returns dict with keys:
    warnings, drug_interactions, contraindications, dosage_and_administration,
    pregnancy, adverse_reactions
    """
    search = f'openfda.generic_name:"{drug_name}"'
    params = {"search": search, "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(OPENFDA_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    data = resp.json()
    results = data.get("results", [])
    if not results:
        return None

    r = results[0]
    out = {
        "warnings": _extract_text(r.get("warnings") or r.get("boxed_warnings")),
        "drug_interactions": _extract_text(r.get("drug_interactions")),
        "contraindications": _extract_text(r.get("contraindications")),
        "dosage_and_administration": _extract_text(r.get("dosage_and_administration")),
        "pregnancy": _extract_text(r.get("pregnancy") or r.get("pregnancy_or_breast_feeding")),
        "adverse_reactions": _extract_text(r.get("adverse_reactions")),
    }
    return out


# ---------------------------------------------------------------------------
# Step 3: Fetch DailyMed SPL metadata
# ---------------------------------------------------------------------------


def fetch_dailymed_spls(drug_name: str) -> list[dict] | None:
    """Fetch DailyMed SPL list for a drug. Returns list of SPL metadata or None."""
    try:
        params = {"drug_name": drug_name, "pagesize": 5}
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(DAILYMED_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        return items if items else None
    except Exception:
        return None  # DailyMed is optional; skip on any error


# ---------------------------------------------------------------------------
# Step 4: Generate 20 questions per drug from FDA data
# ---------------------------------------------------------------------------


def generate_questions(drug_name: str, fda_data: dict[str, str]) -> list[dict]:
    """
    Generate 20 patient-style questions from FDA label data.
    Returns list of dicts with keys: question, category, tags
    """
    questions = []
    w = fda_data.get("warnings") or ""
    di = fda_data.get("drug_interactions") or ""
    c = fda_data.get("contraindications") or ""
    dos = fda_data.get("dosage_and_administration") or ""
    preg = fda_data.get("pregnancy") or ""
    adv = fda_data.get("adverse_reactions") or ""

    # Extract a few keywords from text for tags (simple heuristic)
    def _tags(*texts: str) -> str:
        combined = " ".join(texts).lower()
        words = re.findall(r"\b[a-z]{4,}\b", combined)
        seen = set()
        out = []
        for w in words:
            if w not in seen and w not in {"drug", "drugs", "patient", "patients", "medicine", "medication"}:
                seen.add(w)
                out.append(w)
        return ";".join(out[:5]) if out else drug_name.lower()

    base_tags = f"{drug_name.lower()};fda;label"

    # Warnings
    if w:
        questions.append({
            "question": f"What are the warnings for {drug_name}?",
            "category": "Warnings",
            "tags": base_tags + ";warnings",
        })
        questions.append({
            "question": f"Should I avoid {drug_name} if I have certain conditions?",
            "category": "Warnings",
            "tags": base_tags + ";warnings;contraindications",
        })

    # Drug interactions
    if di:
        questions.append({
            "question": f"Does {drug_name} interact with other medications?",
            "category": "Drug Interactions",
            "tags": base_tags + ";interactions",
        })
        questions.append({
            "question": f"Can I take {drug_name} with blood pressure medication?",
            "category": "Drug Interactions",
            "tags": base_tags + ";interactions;blood pressure",
        })
        questions.append({
            "question": f"Can I take {drug_name} with ibuprofen or aspirin?",
            "category": "Drug Interactions",
            "tags": base_tags + ";interactions;nsaid",
        })

    # Contraindications
    if c:
        questions.append({
            "question": f"Who should not take {drug_name}?",
            "category": "Contraindications",
            "tags": base_tags + ";contraindications",
        })

    # Dosage
    if dos:
        questions.append({
            "question": f"How should I take {drug_name}?",
            "category": "Dosage",
            "tags": base_tags + ";dosage",
        })
        questions.append({
            "question": f"What is the recommended dose of {drug_name}?",
            "category": "Dosage",
            "tags": base_tags + ";dosage",
        })
        questions.append({
            "question": f"Can I take {drug_name} with food?",
            "category": "Dosage",
            "tags": base_tags + ";dosage;food",
        })

    # Pregnancy
    if preg:
        questions.append({
            "question": f"Is {drug_name} safe during pregnancy?",
            "category": "Pregnancy",
            "tags": base_tags + ";pregnancy",
        })

    # Adverse reactions
    if adv:
        questions.append({
            "question": f"What are the side effects of {drug_name}?",
            "category": "Adverse Reactions",
            "tags": base_tags + ";side effects",
        })
        questions.append({
            "question": f"Does {drug_name} cause drowsiness?",
            "category": "Adverse Reactions",
            "tags": base_tags + ";drowsiness",
        })

    # Generic questions (always add to reach target)
    generic = [
        (f"Can I take {drug_name} with alcohol?", "Drug Interactions", "alcohol;interactions"),
        (f"Can I take {drug_name} with my other prescriptions?", "Drug Interactions", "interactions"),
        (f"How do I store {drug_name}?", "Storage", "storage"),
        (f"What if I miss a dose of {drug_name}?", "Dosage", "missed dose"),
        (f"Is {drug_name} safe for children?", "Children", "children;pediatric"),
        (f"Can I take {drug_name} if I have kidney disease?", "Special Populations", "kidney"),
        (f"Can I take {drug_name} if I have liver disease?", "Special Populations", "liver"),
        (f"Does {drug_name} expire? How long can I keep it?", "Storage", "expiration"),
        (f"What should I do if I take too much {drug_name}?", "Overdose", "overdose"),
    ]
    for q, cat, tags in generic:
        if len(questions) >= QUESTIONS_PER_DRUG:
            break
        if not any(x["question"] == q for x in questions):
            questions.append({"question": q, "category": cat, "tags": base_tags + ";" + tags})

    # Pad to QUESTIONS_PER_DRUG if needed
    while len(questions) < QUESTIONS_PER_DRUG:
        questions.append({
            "question": f"What should I know about {drug_name} before taking it?",
            "category": "General",
            "tags": base_tags,
        })
        if len(questions) >= QUESTIONS_PER_DRUG:
            break

    return questions[:QUESTIONS_PER_DRUG]


# ---------------------------------------------------------------------------
# Step 5: Save CSV and seed PostgreSQL
# ---------------------------------------------------------------------------


def save_csv(rows: list[dict], path: Path) -> None:
    """Write questions to CSV with columns: id, question, category, tags."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "question", "category", "tags"])
        writer.writeheader()
        for i, r in enumerate(rows, start=1):
            writer.writerow({
                "id": i,
                "question": r["question"],
                "category": r.get("category", "General"),
                "tags": r.get("tags", ""),
            })


def seed_postgres(rows: list[dict]) -> None:
    """Insert questions into PostgreSQL. Requires DATABASE_URL in .env."""
    load_dotenv()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL not set. Skipping PostgreSQL seed.")
        return
    if "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    from datetime import datetime, timezone

    from sqlalchemy import ARRAY, Column, Integer, MetaData, Table, Text, create_engine
    from sqlalchemy.dialects.postgresql import VARCHAR

    engine = create_engine(url, future=True, pool_pre_ping=True)
    metadata = MetaData()
    from sqlalchemy import DateTime

    questions = Table(
        "questions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("question", Text, nullable=False),
        Column("category", VARCHAR(50), nullable=True),
        Column("tags", ARRAY(Text), nullable=True),
        Column("answer", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=True),
        extend_existing=True,
    )

    # Get next ID for appending (table may not exist yet)
    with engine.connect() as conn:
        from sqlalchemy import text
        try:
            r = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM questions"))
            start_id = int(r.scalar() or 0) + 1
        except Exception:
            start_id = 1

    now = datetime.now(timezone.utc)
    insert_rows = []
    for i, r in enumerate(rows):
        tags_str = r.get("tags", "")
        tags_list = [t.strip() for t in str(tags_str).split(";") if t.strip()]
        insert_rows.append({
            "id": start_id + i,
            "question": r["question"],
            "category": r.get("category", "General"),
            "tags": tags_list,
            "answer": None,
            "created_at": now,
        })

    with engine.begin() as conn:
        conn.execute(questions.insert(), insert_rows)

    print(f"Seeded {len(rows)} questions to PostgreSQL (ids {start_id}..{start_id + len(rows) - 1})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()
    here = Path(__file__).resolve().parent

    print("Step 1: Fetching drug names from OpenFDA (drugs with labels)...")
    drugs = fetch_drug_names(limit=MAX_DRUGS)
    print(f"  Got {len(drugs)} drugs")

    all_questions = []
    skipped = 0
    drugs_to_process = drugs[:MAX_DRUGS]  # Stop after MAX_DRUGS

    for i, drug in enumerate(drugs_to_process, start=1):
        print(f"\n[{i}/{len(drugs_to_process)}] {drug}...")

        time.sleep(DELAY_SECONDS)

        fda_data = fetch_openfda_label(drug)
        if not fda_data or not any(fda_data.values()):
            print(f"  SKIP: No FDA data for {drug}")
            skipped += 1
            continue

        time.sleep(DELAY_SECONDS)
        dailymed = fetch_dailymed_spls(drug)
        # DailyMed optional; we use FDA data for questions

        qs = generate_questions(drug, fda_data)
        all_questions.extend(qs)
        print(f"  Generated {len(qs)} questions (total: {len(all_questions)})")

    if not all_questions:
        print("\nNo questions generated. Exiting.")
        return

    csv_path = here / "mega_questions.csv"
    print(f"\nStep 5: Saving {len(all_questions)} questions to {csv_path}")
    save_csv(all_questions, csv_path)

    print("Seeding PostgreSQL...")
    seed_postgres(all_questions)

    print(f"\nDone. {len(all_questions)} questions | {len(drugs_to_process) - skipped} drugs with data | {skipped} skipped")


if __name__ == "__main__":
    main()
