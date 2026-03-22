"""
RxBuddy Services — RxNorm API Client
======================================

Thin, spec-compliant wrapper around the NLM RxNav REST API.

Functions
---------
search_drug_by_name(name)  → hits /drugs.json, returns {rxcui, name, synonyms[]}
get_rxcui(name)            → returns best RxCUI match (exact → ingredient → brand)
get_related_drugs(rxcui)   → returns {generic_name, brand_names[]}

The existing backend/rxnorm_client.py module is not replaced — this module
provides the interface mandated by the normalization pipeline spec.  Where
possible it delegates to the parent module's lru_cached functions to avoid
redundant API calls.

Rate limit: 20 req/s (enforced by parent module throttle).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Delegate to parent module for throttling, caching, and shared HTTP logic
import rxnorm_client as _parent  # noqa: E402 — backend/rxnorm_client.py

logger = logging.getLogger("rxbuddy.services.rxnorm")

_RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"


def search_drug_by_name(name: str) -> dict:
    """
    Search for a drug by name using the RxNorm /drugs.json endpoint.

    Returns a dict with keys:
        rxcui     — first matching RxCUI (empty string if not found)
        name      — canonical RxNorm drug name
        synonyms  — list of related names (brands + generics)

    Falls back gracefully on network errors or no match.

    Example
    -------
    search_drug_by_name("eliquis")
    → {"rxcui": "1364430", "name": "apixaban", "synonyms": ["Eliquis"]}
    """
    if not name or not name.strip():
        return {"rxcui": "", "name": "", "synonyms": []}

    try:
        data = _parent._get(f"{_RXNAV_BASE}/drugs.json", {"name": name})
        drug_group = data.get("drugGroup", {})
        concept_groups = drug_group.get("conceptGroup") or []

        # Priority: IN (ingredient/generic) → BN (brand name)
        for tty_pref in ("IN", "MIN", "BN", "SBD", "GPCK"):
            for group in concept_groups:
                if group.get("tty") != tty_pref:
                    continue
                props = group.get("conceptProperties") or []
                if not props:
                    continue
                first = props[0]
                rxcui = str(first.get("rxcui", ""))
                canonical = first.get("name", "")
                synonyms = [p["name"] for p in props[1:] if p.get("name")]
                return {"rxcui": rxcui, "name": canonical.lower(), "synonyms": synonyms}

        logger.debug("[RxNorm/drugs] No match for '%s'", name)
        return {"rxcui": "", "name": "", "synonyms": []}

    except Exception as exc:
        logger.warning("[RxNorm/drugs] search_drug_by_name failed for '%s': %s", name, exc)
        return {"rxcui": "", "name": "", "synonyms": []}


def get_rxcui(name: str) -> Optional[str]:
    """
    Return the best RxCUI for a drug name.

    Priority: exact match → ingredient concept → brand concept.
    Uses the parent module's lru_cached lookup_rxcui to avoid redundant calls.

    Returns None when no CUI is found.

    Example
    -------
    get_rxcui("Tylenol")       → "202433"
    get_rxcui("acetaminophen") → "161"
    get_rxcui("totally fake")  → None
    """
    if not name or not name.strip():
        return None

    # Fast path: parent module uses lru_cache and handles approximate matching
    rxcui = _parent.lookup_rxcui(name)
    if rxcui:
        return rxcui

    # Fallback: try the /drugs.json endpoint for broader matching
    result = search_drug_by_name(name)
    return result["rxcui"] or None


def get_related_drugs(rxcui: str) -> dict:
    """
    Return brand and generic names related to a given RxCUI.

    Returns a dict:
        generic_name  — canonical generic/ingredient name (first IN concept)
        brand_names   — list of brand names (BN concepts)

    Example
    -------
    get_related_drugs("161")
    → {"generic_name": "acetaminophen", "brand_names": ["Tylenol", "Panadol"]}
    """
    if not rxcui or not rxcui.strip():
        return {"generic_name": "", "brand_names": []}

    # Parent module returns {"brand": [...], "generic": [...]}
    raw = _parent.get_brand_and_generic(rxcui)

    generic_names = raw.get("generic", [])
    brand_names = raw.get("brand", [])

    return {
        "generic_name": generic_names[0].lower() if generic_names else "",
        "brand_names": brand_names,
    }
