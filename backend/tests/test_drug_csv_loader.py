from __future__ import annotations

import csv
import os
import sys

# Ensure backend/ is on the path so sibling imports resolve consistently.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from data.drug_csv_loader import (
    DRUG_CSV_PATH,
    drug_lookup_size,
    get_drug_by_generic,
    load_drug_lookup,
)


def _expected_unique_generic_count() -> int:
    unique_generics: set[str] = set()
    with DRUG_CSV_PATH.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            generic_name = (row.get("generic_name") or "").strip().lower()
            if generic_name:
                unique_generics.add(generic_name)
    return len(unique_generics)


def test_csv_loads_into_non_empty_lookup() -> None:
    lookup = load_drug_lookup()
    assert lookup, "Expected the CSV lookup to contain at least one drug"


def test_lookup_keys_are_lowercase_generic_names() -> None:
    lookup = load_drug_lookup()
    assert "lisinopril" in lookup
    assert all(key == key.lower() for key in lookup)


def test_case_insensitive_generic_lookup_returns_same_object() -> None:
    lower = get_drug_by_generic("lisinopril")
    title = get_drug_by_generic("Lisinopril")
    upper = get_drug_by_generic("LISINOPRIL")

    assert lower is not None
    assert lower is title
    assert lower is upper


def test_lookup_preserves_full_flat_csv_object() -> None:
    record = get_drug_by_generic("Lisinopril")

    assert record is not None
    assert record["generic_name"] == "Lisinopril"
    assert record["common_brand_name"] == "Prinivil"
    assert record["drug_class"] == "ACE Inhibitor"
    assert record["mechanism_simple"]
    assert record["side_effects_simple"]
    assert record["common_use"] == "High blood pressure"


def test_lookup_size_matches_unique_valid_generics_in_csv() -> None:
    lookup = load_drug_lookup()
    expected = _expected_unique_generic_count()

    assert drug_lookup_size() == expected
    assert len(lookup) == expected


def test_unknown_generic_returns_none() -> None:
    assert get_drug_by_generic("not_a_real_drug_name") is None


def test_load_drug_lookup_is_safe_for_startup_warmup() -> None:
    first = load_drug_lookup()
    second = load_drug_lookup()

    assert first is second
    assert drug_lookup_size() > 0
