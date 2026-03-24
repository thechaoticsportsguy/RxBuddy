"""
Pipeline Step 3 — Drug Extraction & Normalization.

Extracts all drug names from a user query and normalises them to
canonical generic names using:
  1. BRAND_TO_GENERIC map (instant, no network)
  2. drug_catalog lookup (brand + generic matching)
  3. services.drug_resolver (Levenshtein + RxNorm fallback)

Returns a list of lowercase canonical generic drug names.
"""
from __future__ import annotations

import logging
import os
import re
import sys

logger = logging.getLogger("rxbuddy.pipeline.drug_extractor")

# Ensure backend/ is importable regardless of how the process was started
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── Brand → generic map (fast path, zero network) ────────────────────────────
BRAND_TO_GENERIC: dict[str, str] = {
    "tylenol": "acetaminophen", "advil": "ibuprofen", "motrin": "ibuprofen",
    "aleve": "naproxen", "bayer": "aspirin", "excedrin": "acetaminophen",
    "benadryl": "diphenhydramine", "claritin": "loratadine", "zyrtec": "cetirizine",
    "allegra": "fexofenadine", "prilosec": "omeprazole", "nexium": "esomeprazole",
    "pepcid": "famotidine", "xanax": "alprazolam", "valium": "diazepam",
    "ambien": "zolpidem", "zoloft": "sertraline", "prozac": "fluoxetine",
    "lexapro": "escitalopram", "lipitor": "atorvastatin", "crestor": "rosuvastatin",
    "viagra": "sildenafil", "cialis": "tadalafil", "synthroid": "levothyroxine",
    "eliquis": "apixaban", "xarelto": "rivaroxaban", "pradaxa": "dabigatran",
    "coumadin": "warfarin", "plavix": "clopidogrel", "glucophage": "metformin",
    "prinivil": "lisinopril", "zestril": "lisinopril", "norvasc": "amlodipine",
    "suboxone": "buprenorphine", "vicodin": "hydrocodone", "percocet": "oxycodone",
    "ultram": "tramadol", "neurontin": "gabapentin", "lyrica": "pregabalin",
    "celebrex": "celecoxib", "voltaren": "diclofenac", "toradol": "ketorolac",
    "indocin": "indomethacin", "mobic": "meloxicam",
}

# Well-known generic names for fast matching (no network call needed)
_KNOWN_GENERICS: frozenset[str] = frozenset({
    "acetaminophen", "ibuprofen", "aspirin", "naproxen", "amoxicillin",
    "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
    "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
    "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
    "hydrocodone", "oxycodone", "tramadol", "warfarin", "ciprofloxacin",
    "clopidogrel", "apixaban", "rivaroxaban", "dabigatran", "digoxin",
    "amiodarone", "lithium", "phenytoin", "carbamazepine", "valproate",
    "methotrexate", "cyclosporine", "tacrolimus", "sildenafil", "tadalafil",
    "nitroglycerin", "isosorbide mononitrate", "linezolid", "doxycycline",
    "minocycline", "isotretinoin", "spironolactone", "potassium chloride",
    "verapamil", "diltiazem", "celecoxib", "meloxicam", "diclofenac",
    "indomethacin", "ketorolac", "buprenorphine", "fentanyl", "morphine",
    "codeine", "diphenhydramine", "loratadine", "cetirizine", "fexofenadine",
    "esomeprazole", "famotidine", "diazepam", "zolpidem", "rosuvastatin",
    "pregabalin", "duloxetine", "venlafaxine", "buspirone", "clonazepam",
    "lorazepam", "heparin", "enoxaparin", "sotalol", "haloperidol",
    "clozapine", "aripiprazole", "quetiapine", "olanzapine", "risperidone",
    "tamoxifen", "methadone", "theophylline", "vancomycin",
})


# ── Slang / misspelling map (zero network) ───────────────────────────────────
SPELLING_FIXES: dict[str, str] = {
    "tynenol": "tylenol", "ibrofen": "ibuprofen", "ibuprofin": "ibuprofen",
    "amoxicilin": "amoxicillin", "acetiminophen": "acetaminophen",
    "acetominophen": "acetaminophen", "ibuprophen": "ibuprofen",
    "naproxin": "naproxen", "sertralin": "sertraline",
    "metforman": "metformin", "lisinipril": "lisinopril",
    "gabapenten": "gabapentin", "omeperazole": "omeprazole",
}

SLANG_MAP: dict[str, str] = {
    "drunk": "alcohol", "wasted": "alcohol", "booze": "alcohol",
    "drinking": "alcohol", "meds": "medication", "pills": "medication",
    "xanny": "xanax", "addy": "adderall", "molly": "MDMA",
}

FILLER_WORDS: frozenset[str] = frozenset({
    "like", "um", "basically", "literally", "yo", "bro", "hey",
})


def normalize_query(query: str) -> tuple[str, str]:
    """
    Pre-process a user query: fix slang, misspellings, remove filler.
    Zero API calls.

    Returns (original_query, cleaned_query).
    """
    original = (query or "").strip()
    if not original:
        return original, original

    q = original.lower()

    # Replace slang phrases (longest first)
    for slang, medical in sorted(SLANG_MAP.items(), key=lambda x: -len(x[0])):
        pattern = r"\b" + re.escape(slang) + r"\b"
        q = re.sub(pattern, medical, q, flags=re.IGNORECASE)

    # Fix drug misspellings
    for wrong, right in SPELLING_FIXES.items():
        pattern = r"\b" + re.escape(wrong) + r"\b"
        q = re.sub(pattern, right, q, flags=re.IGNORECASE)

    # Remove filler words
    words = q.split()
    words = [w for w in words if w.lower() not in FILLER_WORDS]
    cleaned = " ".join(words).strip()

    return original, cleaned if cleaned else original


def extract_drug_names(query: str) -> list[str]:
    """
    Extract ALL drug names from a query. Returns canonical lowercase generics.

    Resolution order (stops accumulating, does not duplicate):
      1. BRAND_TO_GENERIC map (instant)
      2. _KNOWN_GENERICS set (instant)
      3. drug_catalog (brand + canonical name matching)
      4. services.drug_resolver (Levenshtein + RxNorm API fallback)

    Parameters
    ----------
    query : raw or cleaned user query

    Returns
    -------
    List of unique, lowercase, canonical generic drug names.
    """
    q_lower = query.lower()
    found: list[str] = []

    # 1. Brand → generic (fast path, no imports)
    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower and generic not in found:
            found.append(generic)

    # 2. Known generics (fast path, no imports)
    for generic in _KNOWN_GENERICS:
        if generic in q_lower and generic not in found:
            found.append(generic)

    # 3. Full drug catalog (import lazily to avoid circular deps)
    try:
        from drug_catalog import _CATALOG, _ALIAS_MAP
        for canonical_key in _CATALOG:
            if canonical_key in q_lower and canonical_key not in found:
                found.append(canonical_key)
        for alias, canonical_key in _ALIAS_MAP.items():
            if alias in q_lower and canonical_key not in found:
                found.append(canonical_key)
    except Exception:
        pass

    # 4. Resolver fallback — catches misspellings + RxNorm unknowns
    #    Only runs if nothing was found so far (avoids redundant work)
    if not found:
        try:
            from services.drug_resolver import extract_generic_names
            resolved = extract_generic_names(query)
            for g in resolved:
                if g not in found:
                    found.append(g)
        except Exception:
            pass

    return found


def normalize_drug_names(drug_names: list[str]) -> list[str]:
    """
    Normalise a list of drug names via drug_catalog (brand → canonical).
    Returns the canonical generic name for each input.
    """
    normalised: list[str] = []
    for raw in drug_names:
        try:
            from drug_catalog import find_drug
            rec = find_drug(raw)
            normalised.append(rec.canonical_name if rec else raw.lower())
        except Exception:
            normalised.append(raw.lower())
    return normalised
