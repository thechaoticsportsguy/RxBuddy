from __future__ import annotations

import csv
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("rxbuddy.drug_csv_loader")

DRUG_CSV_RELATIVE_PATH = Path("backend") / "data" / "rxbuddy_drugs_2000.csv"
DRUG_CSV_PATH = Path(__file__).resolve().parents[2] / DRUG_CSV_RELATIVE_PATH


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in row.items():
        if raw_key is None:
            continue
        key = raw_key.strip()
        normalized[key] = raw_value.strip() if raw_value is not None else ""
    return normalized


@lru_cache(maxsize=1)
def load_drug_lookup() -> dict[str, dict[str, str]]:
    """
    Load the CSV drug dataset into memory once and return a cached lookup table.

    Keys are lowercase generic names. Values are the full normalized CSV rows.
    """
    lookup: dict[str, dict[str, str]] = {}

    with DRUG_CSV_PATH.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row_number, row in enumerate(reader, start=2):
            normalized_row = _normalize_row(row)
            generic_name = normalized_row.get("generic_name", "")
            key = generic_name.lower()

            if not key:
                logger.warning(
                    "[DrugCsvLoader] Skipping row %d with empty generic_name",
                    row_number,
                )
                continue

            if key in lookup:
                logger.warning(
                    "[DrugCsvLoader] Duplicate generic_name '%s' on row %d; overwriting earlier row",
                    key,
                    row_number,
                )

            lookup[key] = normalized_row

    return lookup


def get_drug_by_generic(name: str) -> dict[str, str] | None:
    """Case-insensitive lookup by generic drug name."""
    key = (name or "").strip().lower()
    if not key:
        return None
    return load_drug_lookup().get(key)


def drug_lookup_size() -> int:
    """Return the number of drugs currently loaded in the CSV lookup."""
    return len(load_drug_lookup())


__all__ = [
    "DRUG_CSV_PATH",
    "DRUG_CSV_RELATIVE_PATH",
    "drug_lookup_size",
    "get_drug_by_generic",
    "load_drug_lookup",
]
