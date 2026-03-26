#!/usr/bin/env python3
"""
Pre-populate the side effects DB for the top drugs.

Calls POST /api/drugs/{drug}/parse-label for each drug in parallel.
Safe to re-run — results are upserted, not duplicated.

Usage:
    python backend/scripts/prepopulate.py
    python backend/scripts/prepopulate.py --url https://your-railway-url.railway.app
    python backend/scripts/prepopulate.py --drugs 50 --workers 5
    python backend/scripts/prepopulate.py --drug metformin   # single drug test
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Top 50 most prescribed drugs in the US
TOP_DRUGS = [
    "metformin", "atorvastatin", "lisinopril", "levothyroxine", "amlodipine",
    "metoprolol", "omeprazole", "losartan", "albuterol", "gabapentin",
    "sertraline", "simvastatin", "montelukast", "fluticasone", "furosemide",
    "acetaminophen", "ibuprofen", "aspirin", "amoxicillin", "azithromycin",
    "doxycycline", "ciprofloxacin", "prednisone", "methylprednisolone",
    "escitalopram", "fluoxetine", "bupropion", "duloxetine", "alprazolam",
    "clonazepam", "zolpidem", "quetiapine", "aripiprazole", "citalopram",
    "hydrochlorothiazide", "amlodipine", "carvedilol", "warfarin", "clopidogrel",
    "rosuvastatin", "pantoprazole", "esomeprazole", "famotidine", "ranitidine",
    "cetirizine", "loratadine", "diphenhydramine", "tramadol", "cyclobenzaprine",
    "naproxen",
]


def parse_one(drug: str, base_url: str, timeout: int = 90) -> tuple[str, bool, str]:
    """
    Call the parse-label endpoint for one drug.
    Returns (drug_name, success, message).
    """
    url = f"{base_url.rstrip('/')}/api/drugs/{drug}/parse-label"
    try:
        resp = requests.post(url, timeout=timeout)
        if resp.status_code == 429:
            return drug, False, f"rate-limited (retry later)"
        data = resp.json()
        status = data.get("status", "")
        if status == "ok":
            count = data.get("side_effects_count", 0)
            brands = ", ".join(data.get("brand_names", [])[:3])
            return drug, True, f"{count} effects parsed{f' ({brands})' if brands else ''}"
        elif status == "no_label":
            return drug, False, "no FDA label found"
        elif status == "parse_failed":
            return drug, False, "Gemini parse failed"
        else:
            return drug, False, f"unexpected status: {status}"
    except requests.Timeout:
        return drug, False, "timed out"
    except Exception as exc:
        return drug, False, str(exc)


def main():
    parser = argparse.ArgumentParser(description="Pre-populate RxBuddy side effects DB")
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Base URL of the RxBuddy backend (default: http://localhost:8000)")
    parser.add_argument("--drugs", type=int, default=20,
                        help="Number of drugs from the top list to process (default: 20)")
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel workers (default: 3, max recommended: 5)")
    parser.add_argument("--drug", type=str, default=None,
                        help="Parse a single specific drug (ignores --drugs)")
    parser.add_argument("--timeout", type=int, default=90,
                        help="Per-drug timeout in seconds (default: 90)")
    args = parser.parse_args()

    # Single-drug mode
    if args.drug:
        drugs = [args.drug.strip().lower()]
    else:
        drugs = TOP_DRUGS[: args.drugs]

    print(f"RxBuddy side effects pre-population")
    print(f"  Target: {args.url}")
    print(f"  Drugs:  {len(drugs)}")
    print(f"  Workers:{args.workers}")
    print()

    # Quick health check
    try:
        health = requests.get(f"{args.url.rstrip('/')}/health", timeout=10)
        if health.status_code != 200:
            print(f"WARNING: /health returned {health.status_code} — continuing anyway")
    except Exception as exc:
        print(f"WARNING: Could not reach {args.url}/health: {exc}")
        print("Make sure the backend is running before running this script.")
        sys.exit(1)

    start = time.time()
    results: list[tuple[str, bool, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(parse_one, drug, args.url, args.timeout): drug
            for drug in drugs
        }
        for future in as_completed(futures):
            drug, success, msg = future.result()
            icon = "✓" if success else "✗"
            print(f"  {icon} {drug:<25} {msg}")
            results.append((drug, success, msg))

    elapsed = time.time() - start
    ok = sum(1 for _, s, _ in results if s)
    failed = len(results) - ok

    print()
    print(f"Done in {elapsed:.1f}s — {ok} succeeded, {failed} failed")

    if failed:
        print("\nFailed drugs:")
        for drug, success, msg in results:
            if not success:
                print(f"  - {drug}: {msg}")


if __name__ == "__main__":
    main()
