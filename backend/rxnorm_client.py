"""
RxNorm + DailyMed API Client — v1
==================================

Interfaces with the NLM RxNav REST API (free, no API key required).
Rate limit: 20 requests/second (enforced via _MIN_INTERVAL throttle).

Endpoints used
--------------
  /rxcui.json?name=          – exact drug name → RxCUI list
  /approximateTerm.json      – fuzzy / misspelled name → ranked candidates
  /rxcui/{id}/properties     – canonical name, TTY, language
  /rxcui/{id}/related        – brand ↔ generic name mapping (tty=IN+BN)

DailyMed endpoint
  https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?drug_name=

Data priority
-------------
  RxNorm CUI → DailyMed setID → OpenFDA label → PubMed abstracts
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

import requests

logger = logging.getLogger("rxbuddy.rxnorm")

_RXNAV_BASE    = "https://rxnav.nlm.nih.gov/REST"
_DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_TIMEOUT       = 6      # seconds per request
_MIN_INTERVAL  = 0.06   # 60 ms gap → stays safely under 20 req/s

_last_call: float = 0.0


def _get(url: str, params: dict | None = None) -> dict:
    """Throttled GET that returns parsed JSON or raises on HTTP error."""
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    resp = requests.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── RxCUI lookup ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def lookup_rxcui(drug_name: str) -> Optional[str]:
    """
    Return the first RxNorm CUI for a drug name, or None.

    Uses search=2 (approximate matching) so minor variations still resolve.
    Result is memoised in-process for the lifetime of the server.

    Examples
    --------
    lookup_rxcui("Tylenol")       → "202433"  (brand → ingredient CUI)
    lookup_rxcui("acetaminophen") → "161"
    lookup_rxcui("atorvastatin")  → "83367"
    """
    name = (drug_name or "").strip()
    if not name:
        return None
    try:
        data = _get(f"{_RXNAV_BASE}/rxcui.json", {"name": name, "search": 2})
        ids = data.get("idGroup", {}).get("rxnormId") or []
        if ids:
            logger.debug("[RxNorm] CUI for '%s': %s", name, ids[0])
            return str(ids[0])
    except Exception as exc:
        logger.warning("[RxNorm] CUI lookup failed for '%s': %s", name, exc)
    return None


@lru_cache(maxsize=2048)
def get_canonical_name(rxcui: str) -> Optional[str]:
    """Return the RxNorm canonical (preferred) name for a CUI."""
    if not rxcui:
        return None
    try:
        data = _get(f"{_RXNAV_BASE}/rxcui/{rxcui}/properties.json")
        return data.get("properties", {}).get("name") or None
    except Exception as exc:
        logger.warning("[RxNorm] Properties lookup failed for CUI %s: %s", rxcui, exc)
    return None


def normalize_drug_name(drug_name: str) -> Optional[str]:
    """
    Resolve a drug name (brand, generic, misspelled) to its RxNorm canonical name.

    Returns None when the name cannot be resolved via RxNorm.

    Examples
    --------
    normalize_drug_name("Tylenol")   → "Acetaminophen"
    normalize_drug_name("lipitor")   → "atorvastatin"
    normalize_drug_name("ibuprofn")  → None  (too misspelled for exact lookup)
    """
    rxcui = lookup_rxcui(drug_name)
    if not rxcui:
        return None
    return get_canonical_name(rxcui)


# ── Approximate / spell-corrected match ──────────────────────────────────────

def approximate_match(drug_name: str, max_entries: int = 5) -> list[dict]:
    """
    Return ranked candidates for a misspelled or partial drug name.

    Each entry: {"rxcui": str, "score": int, "name": str}

    Useful for the drug name spell-check layer — pick candidate[0].name
    as the corrected name when score > 80.

    Examples
    --------
    approximate_match("ibuprofin") → [{"rxcui": "5640", "score": 100, "name": "ibuprofen"}, ...]
    approximate_match("tylenl")    → [{"rxcui": "202433", "score": 95, "name": "Tylenol"}, ...]
    """
    name = (drug_name or "").strip()
    if not name:
        return []
    try:
        data = _get(
            f"{_RXNAV_BASE}/approximateTerm.json",
            {"term": name, "maxEntries": max_entries},
        )
        candidates = data.get("approximateGroup", {}).get("candidate") or []
        return [
            {
                "rxcui": str(c.get("rxcui", "")),
                "score": int(float(c.get("score", 0))),
                "name":  str(c.get("name", "")),
            }
            for c in candidates
            if c.get("rxcui") and c.get("name")
        ]
    except Exception as exc:
        logger.warning("[RxNorm] Approximate match failed for '%s': %s", name, exc)
    return []


def spell_correct_drug(drug_name: str, min_score: int = 80) -> Optional[str]:
    """
    Return the RxNorm-corrected drug name for a potential misspelling,
    or None if no high-confidence match is found.

    Examples
    --------
    spell_correct_drug("ibuprofin")  → "ibuprofen"
    spell_correct_drug("atorvastat") → "atorvastatin"
    spell_correct_drug("xzy123")     → None
    """
    candidates = approximate_match(drug_name, max_entries=3)
    if candidates and candidates[0]["score"] >= min_score:
        return candidates[0]["name"]
    return None


# ── Brand ↔ Generic resolution ───────────────────────────────────────────────

@lru_cache(maxsize=1024)
def get_brand_and_generic(rxcui: str) -> dict[str, list[str]]:
    """
    Return {"brand": [...], "generic": [...]} name lists for a CUI.

    Fetches related concepts with TTY = BN (brand name) and IN (ingredient).

    Examples
    --------
    get_brand_and_generic("161") → {"brand": ["Tylenol", "Panadol"], "generic": ["acetaminophen"]}
    """
    result: dict[str, list[str]] = {"brand": [], "generic": []}
    if not rxcui:
        return result
    try:
        # Pass tty directly in the URL — requests percent-encodes '+' as '%2B'
        # which the RxNorm API rejects with 400.  Embedding it in the URL string
        # keeps the literal '+' delimiter that RxNorm expects.
        data = _get(f"{_RXNAV_BASE}/rxcui/{rxcui}/related.json?tty=BN+IN")
        groups = data.get("relatedGroup", {}).get("conceptGroup") or []
        for group in groups:
            tty = group.get("tty", "")
            concepts = group.get("conceptProperties") or []
            names = [c["name"] for c in concepts if c.get("name")]
            if tty == "BN":
                result["brand"].extend(names)
            elif tty == "IN":
                result["generic"].extend(names)
    except Exception as exc:
        logger.warning("[RxNorm] Brand/generic lookup failed for CUI %s: %s", rxcui, exc)
    return result


# ── DailyMed setID lookup ────────────────────────────────────────────────────

@lru_cache(maxsize=1024)
def get_dailymed_setid(drug_name: str) -> Optional[str]:
    """
    Return the first DailyMed SPL setID for a drug name, or None.

    Used to build direct DailyMed citation URLs:
      https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}

    Examples
    --------
    get_dailymed_setid("warfarin") → "8d24adac-fda8-..."
    """
    name = (drug_name or "").strip()
    if not name:
        return None
    try:
        data = _get(
            f"{_DAILYMED_BASE}/spls.json",
            {"drug_name": name, "pagesize": 1},
        )
        items = data.get("data") or []
        if items:
            sid = items[0].get("setid")
            if sid:
                logger.debug("[DailyMed] setID for '%s': %s", name, sid)
                return str(sid)
    except Exception as exc:
        logger.warning("[DailyMed] setID lookup failed for '%s': %s", name, exc)
    return None
