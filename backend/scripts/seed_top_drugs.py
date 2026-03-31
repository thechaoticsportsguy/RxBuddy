#!/usr/bin/env python3
"""
Seed the PostgreSQL side-effects cache for all drugs in the CSV.

For each drug this script runs the full waterfall:
  1. Skip if already in DB (Tier 1 cache)
  2. Skip if hardcoded fallback exists (Tier 2)
  3. Try DailyMed structured sections (Tier 3)
  4. Try Gemini parse of FDA label (Tier 4)
  5. Store result so Tier 1 catches it next time

Usage:
    python backend/scripts/seed_top_drugs.py            # seed all drugs
    python backend/scripts/seed_top_drugs.py --limit 50 # seed first 50 only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure backend/ is importable
_BACKEND = str(Path(__file__).resolve().parents[1])
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Load .env so DATABASE_URL / GEMINI_API_KEY are available
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass  # dotenv not installed — rely on environment variables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed_top_drugs")


async def seed_drug(drug_name: str) -> bool:
    """Seed one drug into the DB.  Returns True if data was found."""
    from pipeline.api_layer import fetch_fda_label, fetch_dailymed_setid
    from pipeline.side_effects_store import (
        get_from_db,
        get_or_fetch_side_effects,
        _get_class_fallback,
        _DRUG_ALIASES,
    )

    key = drug_name.strip().lower()

    # Already cached?
    if get_from_db(key):
        logger.info("  SKIP (DB cache): %s", drug_name)
        return True

    # Has hardcoded fallback?
    resolved = _DRUG_ALIASES.get(key, key)
    if _get_class_fallback(resolved):
        logger.info("  SKIP (hardcoded): %s", drug_name)
        return True

    logger.info("  SEEDING: %s", drug_name)

    try:
        # Fetch FDA label + DailyMed SET ID in parallel
        (fda_label, raw_label), setid = await asyncio.gather(
            fetch_fda_label(drug_name),
            fetch_dailymed_setid(drug_name),
        )

        result = await get_or_fetch_side_effects(
            drug_name=key,
            fda_label=fda_label,
            raw_label=raw_label,
            dailymed_setid=setid,
        )

        if result:
            n_effects = sum(
                len(t.get("items", []))
                for t in result.get("side_effects", {}).values()
            )
            logger.info("  OK: %s (%d effects)", drug_name, n_effects)
            return True
        else:
            logger.warning("  NO DATA: %s", drug_name)
            return False

    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error("  FAILED: %s — %s", drug_name, e)
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-seed side-effects DB from CSV")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only seed the first N drugs (0 = all)")
    parser.add_argument("--batch", type=int, default=5,
                        help="Concurrent batch size (default 5)")
    args = parser.parse_args()

    # Locate the drug CSV
    from data.drug_csv_loader import DRUG_CSV_PATH
    if not DRUG_CSV_PATH.exists():
        logger.error("CSV not found: %s", DRUG_CSV_PATH)
        return

    import csv
    drugs: list[str] = []
    with DRUG_CSV_PATH.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("generic_name") or "").strip().lower()
            if name:
                drugs.append(name)

    if args.limit > 0:
        drugs = drugs[: args.limit]

    logger.info("Seeding %d drugs from %s ...", len(drugs), DRUG_CSV_PATH.name)

    ok = 0
    failed = 0
    batch_size = args.batch

    for i in range(0, len(drugs), batch_size):
        batch = drugs[i : i + batch_size]
        results = await asyncio.gather(
            *[seed_drug(d) for d in batch], return_exceptions=True
        )
        for r in results:
            if r is True:
                ok += 1
            else:
                failed += 1

        # Small delay between batches to respect API rate limits
        await asyncio.sleep(1.0)
        logger.info(
            "Progress: %d/%d  (ok=%d, failed=%d)",
            min(i + batch_size, len(drugs)),
            len(drugs),
            ok,
            failed,
        )

    logger.info("\nDONE — OK: %d, Failed: %d, Total: %d", ok, failed, len(drugs))


if __name__ == "__main__":
    asyncio.run(main())
