"""
seed_all_drugs.py - Fetch 2000+ drugs from RxNorm + OpenFDA and pre-seed
side-effect answers into RxBuddy's database.

Sources for drug names (deduplicated, merged):
  1. RxNorm Prescribe displaynames API  (~1000 top prescribed drugs)
  2. OpenFDA drug labels (unique generic_name values - thousands)
  3. BASELINE_DRUGS hardcoded list (fallback/baseline)

For each drug:
  - Fetches FDA adverse_reactions section (grounded source of truth)
  - Generates a structured side-effect answer via Claude
  - Saves to the questions DB
  - Rebuilds TF-IDF index at the end

Usage:
    # Step 1: Discover drugs (writes drugs_master_list.json - no Claude calls)
    python backend/seed_all_drugs.py discover

    # Step 2: Review the list, then seed everything
    python backend/seed_all_drugs.py seed

    # Step 2 alt: Seed in batches (e.g. 100 at a time to control cost)
    python backend/seed_all_drugs.py seed --batch-size 100 --batch-offset 0
    python backend/seed_all_drugs.py seed --batch-size 100 --batch-offset 100

    # Seed just one drug (for testing)
    python backend/seed_all_drugs.py seed --drug metformin

    # Dry run (no DB writes, no Claude calls)
    python backend/seed_all_drugs.py seed --dry-run

    # Check progress
    python backend/seed_all_drugs.py status

Cost estimate:
    ~2000 drugs x 1 Claude Sonnet call each = 2000 calls
    At ~500 input + 300 output tokens per call = 1.6M tokens total
    Claude Sonnet pricing: ~$5-8 total for the full run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent          # backend/
PROJECT_ROOT = SCRIPT_DIR.parent                      # RxBuddy/

# Make sure backend/ is importable
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import anthropic
import requests as http_requests
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("seed_all_drugs")

# ── File paths ─────────────────────────────────────────────────────────────────
MASTER_LIST_FILE = PROJECT_ROOT / "drugs_master_list.json"
PROGRESS_FILE    = PROJECT_ROOT / "seed_progress.json"

# ── API URLs ───────────────────────────────────────────────────────────────────
RXNORM_DISPLAYNAMES_URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/displaynames.json"
OPENFDA_LABEL_URL       = "https://api.fda.gov/drug/label.json"

# ── Baseline drugs (used if discover hasn't been run yet) ─────────────────────
BASELINE_DRUGS = [
    "acetaminophen", "ibuprofen", "aspirin", "naproxen", "diphenhydramine",
    "loratadine", "cetirizine", "fexofenadine", "famotidine",
    "omeprazole", "esomeprazole", "metformin", "lisinopril", "atorvastatin",
    "rosuvastatin", "metoprolol", "sertraline", "fluoxetine", "escitalopram",
    "losartan", "amlodipine", "levothyroxine", "gabapentin", "prednisone",
    "sildenafil", "tadalafil", "zolpidem",
    "warfarin", "methotrexate", "lithium", "digoxin", "heparin",
    "phenytoin", "theophylline",
    "oxycodone", "hydrocodone", "alprazolam", "diazepam", "morphine",
    "codeine", "fentanyl", "tramadol",
    "amoxicillin", "azithromycin", "ciprofloxacin", "doxycycline",
    "penicillin", "metronidazole", "clindamycin", "cephalexin",
    "levofloxacin", "sulfamethoxazole",
    "insulin glargine", "glipizide", "simvastatin", "furosemide",
    "hydrochlorothiazide", "dexamethasone", "prednisone", "semaglutide",
    "risperidone", "quetiapine", "olanzapine", "aripiprazole",
    "bupropion", "venlafaxine", "duloxetine", "citalopram",
    "clonazepam", "lorazepam", "buspirone",
]

# ── Junk filter ────────────────────────────────────────────────────────────────
_JUNK_RE = re.compile(
    r"^(water|sodium chloride|dextrose|normal saline|glucose|"
    r"oxygen|nitrogen|carbon dioxide|sterile water|"
    r"vaccine|antigen|allergen|pollen|"
    r"\d+|.{1,2})$",
    re.IGNORECASE,
)
_JUNK_EXACT = frozenset({
    "water", "air", "ice", "salt", "sugar", "alcohol",
    "saline", "dextrose", "lactated ringers",
})
_SALT_SUFFIXES = (
    " hydrochloride", " hcl", " sodium", " potassium",
    " sulfate", " phosphate", " mesylate", " maleate",
    " fumarate", " tartrate", " besylate", " acetate",
    " citrate", " succinate", " bromide", " nitrate",
    " calcium", " magnesium", " chloride",
)
_DOSAGE_WORDS = frozenset({
    "MG", "ML", "MCG", "TABLET", "CAPSULE", "ORAL", "SOLUTION",
    "INJECTION", "EXTENDED", "DELAYED", "RELEASE", "PACK", "KIT",
    "CREAM", "GEL", "OINTMENT", "PATCH", "SPRAY",
})


def _is_valid_drug_name(name: str) -> bool:
    if not name or len(name) < 3:
        return False
    if name.lower() in _JUNK_EXACT:
        return False
    if _JUNK_RE.match(name):
        return False
    if any(c.isdigit() for c in name) and not any(c.isalpha() for c in name):
        return False
    if len(name.split()) > 4:
        return False
    return True


# ── PHASE 1: DISCOVER ─────────────────────────────────────────────────────────

def fetch_rxnorm_drugs(limit: int = 3000) -> list[str]:
    """Fetch drug names from RxNorm Prescribe displaynames endpoint."""
    logger.info("[RxNorm] Fetching drug names...")
    try:
        resp = http_requests.get(
            RXNORM_DISPLAYNAMES_URL,
            headers={"User-Agent": "RxBuddy/2.0"},
            timeout=30,
        )
        resp.raise_for_status()
        terms = resp.json().get("displayTermsList", {}).get("term", [])

        seen: set[str] = set()
        drugs: list[str] = []

        for t in terms[:limit]:
            parts = str(t).strip().split()
            clean: list[str] = []
            for p in parts:
                if p[0].isdigit() or p.upper() in _DOSAGE_WORDS:
                    break
                clean.append(p)
            if not clean:
                continue

            drug_name = " ".join(clean).lower()
            for suffix in _SALT_SUFFIXES:
                if drug_name.endswith(suffix):
                    drug_name = drug_name[: -len(suffix)].strip()

            if drug_name and drug_name not in seen and _is_valid_drug_name(drug_name):
                seen.add(drug_name)
                drugs.append(drug_name)

        logger.info("[RxNorm] Found %d unique drug names", len(drugs))
        return drugs
    except Exception as exc:
        logger.error("[RxNorm] Failed: %s", exc)
        return []


def fetch_openfda_drug_names(pages: int = 40) -> list[str]:
    """Fetch unique generic drug names from OpenFDA label endpoint."""
    logger.info("[OpenFDA] Fetching drug names (%d pages)...", pages)
    seen: set[str] = set()
    drugs: list[str] = []

    for page in range(pages):
        skip = page * 100
        try:
            resp = http_requests.get(
                OPENFDA_LABEL_URL,
                params={"limit": 100, "skip": skip},
                headers={"User-Agent": "RxBuddy/2.0"},
                timeout=15,
            )
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                break

            for label in results:
                for g in label.get("openfda", {}).get("generic_name", []):
                    for name in g.split(","):
                        name = re.sub(r"\s+\d+.*$", "", name.strip().lower()).strip()
                        if name and name not in seen and _is_valid_drug_name(name):
                            seen.add(name)
                            drugs.append(name)

            if page % 10 == 9:
                time.sleep(1)

        except Exception as exc:
            logger.warning("[OpenFDA] Page %d failed: %s", page, exc)
            time.sleep(2)

    logger.info("[OpenFDA] Found %d unique drug names", len(drugs))
    return drugs


def discover_drugs() -> list[dict]:
    """Merge drug names from all sources, deduplicate, return master list."""
    rxnorm  = set(fetch_rxnorm_drugs())
    openfda = set(fetch_openfda_drug_names())
    base    = {d.lower() for d in BASELINE_DRUGS}

    all_names = rxnorm | openfda | base
    logger.info(
        "[Discover] %d RxNorm + %d OpenFDA + %d baseline = %d unique",
        len(rxnorm), len(openfda), len(base), len(all_names),
    )

    master = []
    for name in sorted(all_names):
        sources = []
        if name in rxnorm:  sources.append("rxnorm")
        if name in openfda: sources.append("openfda")
        if name in base:    sources.append("baseline")
        master.append({"name": name, "source": "+".join(sources)})

    return master


# ── PHASE 2: SEED ─────────────────────────────────────────────────────────────

def get_engine():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not set in .env")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, future=True, pool_pre_ping=True)


def question_exists(engine, question_text: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM questions WHERE LOWER(question) = LOWER(:q)"),
            {"q": question_text},
        )
        return int(result.scalar() or 0) > 0


def get_next_id(engine) -> int:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM questions"))
        return int(result.scalar() or 0) + 1


def save_to_db(engine, question: str, answer: str, drug: str) -> int:
    next_id = get_next_id(engine)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO questions (id, question, category, tags, answer, created_at)
                VALUES (:id, :question, :category, :tags, :answer, :created_at)
            """),
            {
                "id":         next_id,
                "question":   question,
                "category":   "Side Effects",
                "tags":       [drug, "side effects", "pre-seeded"],
                "answer":     answer,
                "created_at": datetime.now(timezone.utc),
            },
        )
    return next_id


def fetch_fda_adverse_reactions(drug_name: str) -> str | None:
    """Fetch adverse_reactions section from OpenFDA."""
    for field in ("generic_name", "brand_name", "substance_name"):
        try:
            resp = http_requests.get(
                OPENFDA_LABEL_URL,
                params={"search": f'openfda.{field}:"{drug_name}"', "limit": 1},
                timeout=10,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                continue
            adverse = results[0].get("adverse_reactions", [])
            if adverse and isinstance(adverse, list) and adverse[0].strip():
                return adverse[0][:2000]
        except Exception:
            continue
    return None


# ── Claude answer generation ───────────────────────────────────────────────────
#
# Output format must match what _parse_structured_answer() in main.py expects.
# Parser looks for these labels (case-insensitive, colon or dash separator):
#   VERDICT, ANSWER, WARNING, DETAILS, ACTION, ARTICLE, CONFIDENCE, SOURCES
#
_SEED_SYSTEM_PROMPT = """You are RxBuddy, a patient-facing medication assistant.

Generate a structured side-effect summary for the drug provided.
Return ONLY the fields below, in exactly this format, with no markdown or backticks:

VERDICT: CAUTION
ANSWER: <1-2 sentence plain-English summary of the most common side effects>
WARNING: <1 sentence on when to seek medical help>
DETAILS: <bullet 1> | <bullet 2> | <bullet 3>
ACTION: <action 1> | <action 2> | <action 3>
ARTICLE: <1-2 sentence plain-English explanation of how the drug works>
CONFIDENCE: <HIGH if FDA data provided, MEDIUM if not>
SOURCES: FDA label (DailyMed) | openFDA

Rules:
- VERDICT is ALWAYS "CAUTION" for side-effect questions
- ANSWER: list 3-5 common side effects in plain English
- DETAILS: 2-4 clinical facts patients should know; separate with " | "
- ACTION: 2-3 practical steps; separate with " | "
- WARNING: one sentence about symptoms that require urgent medical attention
- ARTICLE: plain English mechanism of action
- NEVER mention death, overdose, addiction, or off-label use in ANSWER or DETAILS
- Base ANSWER only on the FDA ADVERSE REACTIONS data provided
- If no FDA data, state you could not find verified side-effect data for this drug
"""


def generate_answer(drug_name: str, fda_text: str | None) -> str | None:
    """Generate a structured side-effect answer via Claude."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key, timeout=45.0)

    fda_block = (
        f"\n\nFDA ADVERSE REACTIONS for {drug_name.upper()}:\n{fda_text}"
        if fda_text else ""
    )
    confidence = "HIGH" if fda_text else "MEDIUM"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=_SEED_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"What are the side effects of {drug_name}?{fda_block}",
            }],
        )
        raw = (response.content[0].text or "").strip()

        # Strip code fences if Claude wraps anyway
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        # Sanity check: must contain at least VERDICT and ANSWER
        if "VERDICT" not in raw.upper() or "ANSWER" not in raw.upper():
            logger.warning("[Claude] Unexpected format for %s — wrapping raw text", drug_name)
            return (
                f"VERDICT: CAUTION\n"
                f"ANSWER: {raw[:400]}\n"
                f"CONFIDENCE: {confidence}\n"
                f"SOURCES: FDA label (DailyMed) | openFDA"
            )

        # Ensure CONFIDENCE line is present (Claude sometimes omits it)
        if "CONFIDENCE" not in raw.upper():
            raw += f"\nCONFIDENCE: {confidence}"
        if "SOURCES" not in raw.upper():
            raw += "\nSOURCES: FDA label (DailyMed) | openFDA"

        return raw

    except Exception as exc:
        logger.error("[Claude] Failed for %s: %s", drug_name, exc)
        return None


# ── Progress tracking ──────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": [], "failed": [], "no_fda": [], "skipped": []}


def save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


# ── Main seed loop ─────────────────────────────────────────────────────────────

def seed_drugs(
    drugs: list[dict],
    dry_run: bool = False,
    batch_size: int | None = None,
    batch_offset: int = 0,
    delay: float = 1.0,
    require_fda: bool = False,
) -> dict:
    engine = get_engine() if not dry_run else None
    progress = load_progress()
    completed_set = set(progress["completed"])

    if batch_size:
        drugs = drugs[batch_offset: batch_offset + batch_size]
        logger.info("[Seed] Batch: offset=%d size=%d", batch_offset, len(drugs))

    stats = {"seeded": 0, "skipped": 0, "failed": 0, "no_fda": 0, "existed": 0}

    logger.info("=" * 60)
    logger.info("RxBuddy Drug Seeder - %d drugs to process", len(drugs))
    logger.info("Dry run: %s | Require FDA: %s | Delay: %.1fs", dry_run, require_fda, delay)
    logger.info("=" * 60)

    for i, drug_entry in enumerate(drugs, 1):
        drug    = drug_entry["name"]
        question = f"What are the side effects of {drug}?"

        if drug in completed_set:
            logger.info("[%d/%d] %s - already completed", i, len(drugs), drug)
            stats["skipped"] += 1
            continue

        if not dry_run and question_exists(engine, question):
            logger.info("[%d/%d] %s - already in DB", i, len(drugs), drug)
            stats["existed"] += 1
            progress["completed"].append(drug)
            save_progress(progress)
            continue

        logger.info("[%d/%d] %s - fetching FDA data...", i, len(drugs), drug)
        fda_text = fetch_fda_adverse_reactions(drug)

        if fda_text:
            logger.info("  + FDA adverse_reactions: %d chars", len(fda_text))
        else:
            logger.info("  ! No FDA data found")
            stats["no_fda"] += 1
            progress["no_fda"].append(drug)
            if require_fda:
                logger.info("  - Skipping (--require-fda)")
                stats["skipped"] += 1
                save_progress(progress)
                continue

        if dry_run:
            logger.info("  [DRY RUN] Would generate + save answer")
            stats["seeded"] += 1
            continue

        answer = generate_answer(drug, fda_text)
        if not answer:
            logger.error("  x Claude generation failed")
            stats["failed"] += 1
            progress["failed"].append(drug)
            save_progress(progress)
            continue

        try:
            new_id = save_to_db(engine, question, answer, drug)
            logger.info("  > Saved as Q#%d", new_id)
            stats["seeded"] += 1
            progress["completed"].append(drug)
        except Exception as exc:
            logger.error("  x DB save failed: %s", exc)
            stats["failed"] += 1
            progress["failed"].append(drug)

        save_progress(progress)

        if i < len(drugs):
            time.sleep(delay)

    # Rebuild TF-IDF after seeding
    if not dry_run and stats["seeded"] > 0:
        logger.info("Rebuilding TF-IDF index...")
        try:
            from ml.tfidf_search import rebuild_index
            rebuild_index()
            logger.info("TF-IDF index rebuilt")
        except Exception as exc:
            logger.warning("TF-IDF rebuild failed: %s (run manually)", exc)

    logger.info("=" * 60)
    logger.info("RESULTS:")
    logger.info("  Seeded:     %d", stats["seeded"])
    logger.info("  Existed:    %d", stats["existed"])
    logger.info("  Skipped:    %d", stats["skipped"])
    logger.info("  No FDA:     %d", stats["no_fda"])
    logger.info("  Failed:     %d", stats["failed"])
    logger.info("=" * 60)

    return stats


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RxBuddy Drug Seeder - populate 2000+ side-effect answers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backend/seed_all_drugs.py discover               # Step 1: Build drug list
  python backend/seed_all_drugs.py seed --dry-run         # Step 2: Preview
  python backend/seed_all_drugs.py seed                   # Step 3: Run it
  python backend/seed_all_drugs.py seed --batch-size 200  # In batches
  python backend/seed_all_drugs.py seed --drug metformin  # Single drug test
  python backend/seed_all_drugs.py status                 # Check progress
  python backend/seed_all_drugs.py reset                  # Clear progress file
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="Fetch drug names from RxNorm + OpenFDA -> drugs_master_list.json")

    seed_p = sub.add_parser("seed", help="Generate + save side-effect answers")
    seed_p.add_argument("--dry-run",       action="store_true",  help="Preview without DB writes or Claude calls")
    seed_p.add_argument("--drug",          type=str,             help="Seed just one drug (for testing)")
    seed_p.add_argument("--batch-size",    type=int,             help="Process N drugs at a time")
    seed_p.add_argument("--batch-offset",  type=int, default=0,  help="Start from this index in the list")
    seed_p.add_argument("--delay",         type=float, default=1.0, help="Seconds between Claude calls (default 1.0)")
    seed_p.add_argument("--require-fda",   action="store_true",  help="Skip drugs without FDA adverse_reactions data")

    sub.add_parser("status", help="Show seeding progress")
    sub.add_parser("reset",  help="Clear progress file (does NOT delete DB rows)")

    args = parser.parse_args()

    if args.command == "discover":
        logger.info("Discovering drugs from RxNorm + OpenFDA...")
        master = discover_drugs()
        MASTER_LIST_FILE.write_text(json.dumps(master, indent=2))
        logger.info("Wrote %d drugs to %s", len(master), MASTER_LIST_FILE)
        logger.info("Next: review the file, then run 'seed'")

    elif args.command == "seed":
        if args.drug:
            drugs = [{"name": args.drug.strip().lower(), "source": "manual"}]
        elif MASTER_LIST_FILE.exists():
            drugs = json.loads(MASTER_LIST_FILE.read_text())
            logger.info("Loaded %d drugs from %s", len(drugs), MASTER_LIST_FILE)
        else:
            logger.info("No master list found - using %d baseline drugs", len(BASELINE_DRUGS))
            logger.info("Run 'discover' first for 2000+ drugs")
            drugs = [{"name": d, "source": "baseline"} for d in BASELINE_DRUGS]

        seed_drugs(
            drugs,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            batch_offset=args.batch_offset,
            delay=args.delay,
            require_fda=args.require_fda,
        )

    elif args.command == "status":
        if not MASTER_LIST_FILE.exists():
            print("No master list found. Run 'discover' first.")
            return
        master   = json.loads(MASTER_LIST_FILE.read_text())
        progress = load_progress()
        remaining = len(master) - len(progress["completed"]) - len(progress["failed"])
        print(f"\nMaster list:  {len(master)} drugs")
        print(f"Completed:    {len(progress['completed'])}")
        print(f"Failed:       {len(progress['failed'])}")
        print(f"No FDA data:  {len(progress['no_fda'])}")
        print(f"Remaining:    {remaining}")
        if progress["failed"]:
            sample = ", ".join(progress["failed"][:20])
            print(f"\nFailed drugs: {sample}")
            if len(progress["failed"]) > 20:
                print(f"  ... and {len(progress['failed']) - 20} more")

    elif args.command == "reset":
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
            print("Progress reset.")
        else:
            print("No progress file to reset.")


if __name__ == "__main__":
    main()
