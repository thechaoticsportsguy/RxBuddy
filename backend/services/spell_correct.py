"""
RxBuddy Spell Corrector — offline Levenshtein-based drug name normalization
============================================================================

normalize_drug_name(input) → canonical generic name

Resolution order
----------------
1. Exact match against any known name (canonical, brand, misspelling) → generic
2. Levenshtein distance ≤ 2 against all catalog names → generic
3. No match → return input unchanged

No API calls, no external dependencies. All lookups run in <1 ms.

Examples
--------
normalize_drug_name("tylenol")      → "acetaminophen"  (exact brand match)
normalize_drug_name("elyquis")      → "apixaban"        (dist-1 from "eliquis")
normalize_drug_name("rosuvastin")   → "rosuvastatin"    (dist-2 insertion)
normalize_drug_name("verampril")    → "verapamil"       (explicit misspelling)
normalize_drug_name("atorvastatin") → "atorvastatin"    (exact canonical)
normalize_drug_name("xyzzy123")     → "xyzzy123"        (no match)
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Optional

# Ensure backend/ is on sys.path so sibling modules resolve correctly
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from data.drug_catalog import get_exact_to_generic, get_fuzzy_pairs  # noqa: E402

_EXACT: dict[str, str] = get_exact_to_generic()
_FUZZY: list[tuple[str, str]] = get_fuzzy_pairs()

# Maximum Levenshtein distance to accept a fuzzy match
_MAX_DIST = 2

# Minimum input length to attempt fuzzy matching
# (very short strings produce too many false positives)
_MIN_FUZZY_LEN = 4


def _levenshtein(a: str, b: str) -> int:
    """
    Standard dynamic-programming Levenshtein distance.

    Early-exit optimisation: if lengths differ by more than _MAX_DIST,
    return _MAX_DIST + 1 immediately (no match possible).
    """
    if abs(len(a) - len(b)) > _MAX_DIST:
        return _MAX_DIST + 1

    m, n = len(a), len(b)
    # Use two rows instead of a full matrix to save memory
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        row_min = curr[0]
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,       # insert
                prev[j] + 1,           # delete
                prev[j - 1] + cost,    # substitute
            )
            if curr[j] < row_min:
                row_min = curr[j]
        # If the minimum edit in this row already exceeds our threshold,
        # the final distance will be at least that large — prune early.
        if row_min > _MAX_DIST:
            return _MAX_DIST + 1
        prev, curr = curr, prev

    return prev[n]


@lru_cache(maxsize=4096)
def normalize_drug_name(name: str) -> str:
    """
    Resolve any drug name input (brand, generic, misspelling) to the canonical
    generic name from the catalog.

    Returns the input unchanged if no match is found.
    Thread-safe: lru_cache is GIL-protected in CPython.
    """
    if not name:
        return name

    key = name.strip().lower()

    # 1. Exact match (covers: correct generic, brand names, explicit misspellings)
    if key in _EXACT:
        return _EXACT[key]

    # 2. Fuzzy match via Levenshtein (only for inputs long enough to be meaningful)
    if len(key) < _MIN_FUZZY_LEN:
        return name

    best_dist = _MAX_DIST + 1
    best_generic: Optional[str] = None
    best_catalog_name: str = ""

    for catalog_name, generic in _FUZZY:
        # Skip candidates whose length difference already precludes a match
        if abs(len(catalog_name) - len(key)) > _MAX_DIST:
            continue
        d = _levenshtein(key, catalog_name)
        if d < best_dist:
            best_dist = d
            best_generic = generic
            best_catalog_name = catalog_name
            if d == 0:  # can't do better
                break

    if best_generic is not None and best_dist <= _MAX_DIST:
        # Similarity gate: require at least 70% character-level similarity.
        # IMPORTANT: compare against the *matched catalog name* length, not the
        # resolved generic name.  Using the generic length causes false positives:
        # e.g. "hate" → distance-2 match against "hctz" (4 chars) → generic is
        # "hydrochlorothiazide" (19 chars) → similarity = 1-2/19 = 0.89, passes.
        # Correct: similarity = 1-2/max(4,4) = 0.50, correctly rejected.
        match_len = max(len(key), len(best_catalog_name))
        similarity = 1.0 - (best_dist / match_len) if match_len > 0 else 0.0
        if similarity >= 0.70:
            return best_generic
        # Below 70% similarity — reject the fuzzy match

    # 3. No match — return input as-is so callers can still try RxNorm API
    return name


def normalize_query_drugs(query: str) -> list[str]:
    """
    Extract and normalize all drug names mentioned in a free-text query.

    Tokenizes the query, skips common stop words and short tokens, then
    attempts to resolve each token as a drug name.  Returns deduplicated
    list of resolved generic names (preserving order of first occurrence).

    This is a *best-effort* heuristic — it will not find every drug in
    every query.  The full drug_resolver.resolve_query_drugs() adds RxNorm
    API fallback for tokens that don't match the local catalog.
    """
    _STOP = frozenset({
        "can", "i", "take", "is", "it", "with", "and", "the", "a", "an",
        "while", "during", "for", "my", "of", "in", "to", "how", "much",
        "per", "day", "safe", "every", "on", "at", "half", "long", "term",
        "what", "are", "does", "should", "will", "get", "be", "do", "that",
        "this", "when", "if", "not", "use", "after", "before", "between",
        "about", "drug", "medication", "medicine", "pill", "tablet", "capsule",
    })

    tokens = query.lower().split()
    seen: set[str] = set()
    result: list[str] = []

    for tok in tokens:
        tok = tok.strip("'\".,!?;:-()")
        if len(tok) < 4 or tok in _STOP:
            continue
        resolved = normalize_drug_name(tok)
        # Only include the token if normalization produced a known catalog name
        # (i.e., the result differs from input, or input was already canonical)
        if resolved in _EXACT.values() and resolved not in seen:
            seen.add(resolved)
            result.append(resolved)

    return result
