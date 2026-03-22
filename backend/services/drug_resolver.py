"""
RxBuddy Services — Drug Resolver
==================================

resolve_drug(input)           → full resolution dict or CONSULT fallback
resolve_query_drugs(query)    → list of resolved drugs from a free-text query

Resolution flow for resolve_drug(name)
---------------------------------------
1. normalize_drug_name(input)  — offline Levenshtein spell-correction
2. Search drug_catalog          — catalog lookup via find_drug()
3. RxNorm API fallback          — if catalog returns nothing
4. CONSULT fallback             — if all three methods fail

Output schema (success)
-----------------------
{
    "input":     str,    # original user input
    "corrected": str,    # after spell-correct
    "rxcui":     str,    # RxNorm CUI (may be empty)
    "generic":   str,    # canonical generic name
    "brands":    [str],  # known brand names
    "synonyms":  [str],  # other generic/synonym names
}

Output schema (CONSULT fallback)
---------------------------------
{
    "verdict": "CONSULT",
    "answer":  str,
}
"""

from __future__ import annotations

import logging
import os
import re
import sys
from functools import lru_cache
from typing import Optional

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.spell_correct import normalize_drug_name  # noqa: E402
from services.rxnorm_client import get_rxcui, get_related_drugs  # noqa: E402

logger = logging.getLogger("rxbuddy.services.drug_resolver")

_CONSULT_ANSWER = (
    "We could not confidently identify this medication. "
    "Please double-check the drug name or consult a pharmacist."
)

# Words that are NOT drug names — skip these when parsing a query
_NON_DRUG_TOKENS = frozenset({
    # question words / connectors
    "can", "i", "take", "is", "it", "with", "and", "the", "a", "an",
    "while", "during", "for", "my", "of", "in", "to", "how", "much",
    "per", "day", "safe", "every", "on", "at", "half", "long", "term",
    "what", "are", "does", "should", "will", "get", "be", "do", "that",
    "this", "when", "if", "not", "use", "after", "before", "between",
    "about", "also", "been", "have", "has", "had", "was", "were", "its",
    "their", "together", "same", "time", "both", "using", "taking",
    "stop", "start", "continue", "suddenly", "which", "would", "could",
    "might", "okay", "fine", "alright", "help", "need", "want", "know",
    "think", "feel", "which", "than", "then", "just", "even", "very",
    "more", "less", "some", "most", "only", "also", "here", "there",
    "from", "into", "over", "such", "your", "they", "them", "been",
    # medical/clinical context words (not drug names)
    "drug", "medication", "medicine", "pill", "tablet", "capsule", "dose",
    "dosage", "symptoms", "condition", "treatment", "chronic", "acute",
    "interaction", "side", "effect", "effects", "adverse", "reaction",
    "pregnant", "pregnancy", "breastfeed", "nursing", "alcohol", "food",
    "drink", "liver", "kidney", "blood", "heart", "risk", "dangerous",
    "doctor", "pharmacist", "hospital", "clinic", "patient", "health",
    "medical", "clinical", "therapy", "dose", "dosing", "milligram",
    # common English words that could confuse the matcher
    "today", "tomorrow", "yesterday", "walk", "away", "good", "okay",
    "fine", "high", "down", "back", "pain", "hurt", "sore", "ache",
    "feel", "sick", "well", "better", "worse", "same", "like", "want",
    "need", "help", "call", "tell", "show", "find", "look", "make",
    "come", "work", "know", "said", "used", "year", "week", "hour",
})


def _catalog_lookup(name: str) -> Optional[dict]:
    """
    Look up a drug name in the compiled drug_catalog (DrugRecord).
    Returns a standardised dict or None.
    """
    try:
        from drug_catalog import find_drug  # backend/drug_catalog.py (DrugRecord)
        record = find_drug(name)
        if record is None:
            return None
        return {
            "rxcui":    record.rxcui or "",
            "generic":  record.canonical_name.lower(),
            "brands":   record.brand_names,
            "synonyms": record.generic_names,
        }
    except Exception as exc:
        logger.debug("[Resolver] catalog lookup failed for '%s': %s", name, exc)
        return None


def _rxnorm_lookup(name: str) -> Optional[dict]:
    """
    Fallback: query RxNorm API.  Returns a standardised dict or None.
    Network call — result is cached by the underlying lru_cache.
    """
    try:
        rxcui = get_rxcui(name)
        if not rxcui:
            return None
        related = get_related_drugs(rxcui)
        generic = related.get("generic_name") or name.lower()
        brands = related.get("brand_names") or []
        return {
            "rxcui":    rxcui,
            "generic":  generic,
            "brands":   brands,
            "synonyms": [],
        }
    except Exception as exc:
        logger.debug("[Resolver] RxNorm lookup failed for '%s': %s", name, exc)
        return None


@lru_cache(maxsize=2048)
def resolve_drug(name: str) -> dict:
    """
    Resolve any drug name input to a canonical record.

    Parameters
    ----------
    name : str
        A single drug name or search term (brand, generic, misspelling,
        or entirely unrecognisable string).

    Returns
    -------
    dict
        Success:  {"input", "corrected", "rxcui", "generic", "brands", "synonyms"}
        Failure:  {"verdict": "CONSULT", "answer": str}

    Examples
    --------
    resolve_drug("eliquis")           → generic == "apixaban"
    resolve_drug("verampril")         → generic == "verapamil"
    resolve_drug("rosuvastin")        → generic == "rosuvastatin"
    resolve_drug("tylenol")           → generic == "acetaminophen"
    resolve_drug("ozempic")           → generic == "semaglutide"
    resolve_drug("totally fake drug") → {"verdict": "CONSULT", ...}
    """
    if not name or not name.strip():
        return {"verdict": "CONSULT", "answer": _CONSULT_ANSWER}

    original = name.strip()

    # ── Step 1: offline spell-correct ─────────────────────────────────────────
    corrected = normalize_drug_name(original)
    logger.debug("[Resolver] '%s' → corrected='%s'", original, corrected)

    # ── Step 2: catalog lookup (try corrected, then original if different) ────
    hit = _catalog_lookup(corrected)
    if hit is None and corrected.lower() != original.lower():
        hit = _catalog_lookup(original)

    if hit is not None:
        logger.debug("[Resolver] catalog hit for '%s': generic=%s", original, hit["generic"])
        return {
            "input":     original,
            "corrected": corrected,
            **hit,
        }

    # ── Step 3: RxNorm API (try corrected, then original) ─────────────────────
    hit = _rxnorm_lookup(corrected)
    if hit is None and corrected.lower() != original.lower():
        hit = _rxnorm_lookup(original)

    if hit is not None:
        logger.info("[Resolver] RxNorm hit for '%s': generic=%s", original, hit["generic"])
        return {
            "input":     original,
            "corrected": corrected,
            **hit,
        }

    # ── Step 4: CONSULT fallback ──────────────────────────────────────────────
    logger.info("[Resolver] Could not resolve '%s' — returning CONSULT", original)
    return {"verdict": "CONSULT", "answer": _CONSULT_ANSWER}


def resolve_query_drugs(query: str) -> list[dict]:
    """
    Extract and resolve all drug names from a free-text query.

    Tokenises the query, filters stop words, and attempts to resolve each
    candidate token (and adjacent bi-gram for multi-word drug names like
    "insulin glargine").

    Returns a list of resolved drug dicts (CONSULT dicts excluded).
    If the list is empty, no recognisable drugs were found.

    Example
    -------
    resolve_query_drugs("can i take eliquis with ibuprofen")
    → [
        {"generic": "apixaban",  "brands": ["Eliquis"], ...},
        {"generic": "ibuprofen", "brands": ["Advil", ...], ...},
      ]
    """
    tokens = re.split(r"[\s,;/]+", (query or "").lower())
    tokens = [t.strip("'\".,!?;:()") for t in tokens]
    tokens = [t for t in tokens if len(t) >= 4 and t not in _NON_DRUG_TOKENS]

    seen_generics: set[str] = set()
    results: list[dict] = []

    def _try(candidate: str) -> bool:
        r = resolve_drug(candidate)
        if r.get("verdict") == "CONSULT":
            return False
        generic = r.get("generic", "")
        if generic and generic not in seen_generics:
            seen_generics.add(generic)
            results.append(r)
            return True
        return False

    i = 0
    while i < len(tokens):
        # Try bi-gram first (e.g., "insulin glargine")
        if i + 1 < len(tokens):
            bigram = f"{tokens[i]} {tokens[i + 1]}"
            if _try(bigram):
                i += 2
                continue
        _try(tokens[i])
        i += 1

    return results


def extract_generic_names(query: str) -> list[str]:
    """
    Convenience function: return just the list of resolved generic names
    from a query (no CONSULT entries, no duplicates).

    Falls back gracefully if the resolution pipeline raises.
    """
    try:
        return [r["generic"] for r in resolve_query_drugs(query) if r.get("generic")]
    except Exception as exc:
        logger.warning("[Resolver] extract_generic_names failed for query: %s", exc)
        return []
