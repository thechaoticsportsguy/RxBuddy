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


# ── Extended RxNorm functions ──────────────────────────────────────────────────

import requests as _http  # noqa: E402 — standard stdlib-level import


def _rxnav_get(path: str, params: dict | None = None) -> dict:
    """GET a RxNav REST endpoint; return parsed JSON or {} on any failure."""
    try:
        resp = _http.get(
            f"{_RXNAV_BASE}{path}",
            params=params or {},
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=5,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("[RxNav] GET %s failed: %s", path, exc)
        return {}


def approximate_match(term: str) -> list[dict]:
    """
    Fuzzy drug name lookup via RxNav /approximateTerm.json.

    Returns a list of candidate dicts, each with:
        rxcui  — RxCUI string
        name   — drug name
        score  — match score (int)
    Returns [] on failure or no match.

    Example
    -------
    approximate_match("ibuprofin") → [{"rxcui": "5640", "name": "ibuprofen", "score": 64}]
    """
    if not term or not term.strip():
        return []
    try:
        data = _rxnav_get("/approximateTerm.json", {"term": term.strip(), "maxEntries": "5"})
        candidates = data.get("approximateGroup", {}).get("candidate", []) or []
        return [
            {
                "rxcui": c.get("rxcui", ""),
                "name":  c.get("name", ""),
                "score": int(c.get("score", 0)),
            }
            for c in candidates
            if c.get("rxcui")
        ]
    except Exception as exc:
        logger.warning("[RxNav] approximate_match('%s') failed: %s", term, exc)
        return []


def get_prescribable_drugs(name: str) -> list[dict]:
    """
    Return currently-prescribable US drug products matching a name.

    Uses /Prescribe/drugs.json.
    Returns a list of dicts with keys: rxcui, name, tty (term type).
    Returns [] on failure or no match.

    Example
    -------
    get_prescribable_drugs("metformin") → [{"rxcui": "...", "name": "...", "tty": "SBD"}, ...]
    """
    if not name or not name.strip():
        return []
    try:
        data = _rxnav_get("/Prescribe/drugs.json", {"name": name.strip()})
        groups = data.get("drugGroup", {}).get("conceptGroup", []) or []
        results = []
        for group in groups:
            for prop in group.get("conceptProperties", []) or []:
                rxcui = prop.get("rxcui", "")
                drug_name = prop.get("name", "")
                tty = prop.get("tty", "")
                if rxcui and drug_name:
                    results.append({"rxcui": rxcui, "name": drug_name, "tty": tty})
        return results
    except Exception as exc:
        logger.warning("[RxNav] get_prescribable_drugs('%s') failed: %s", name, exc)
        return []


def get_rxterms(rxcui: str) -> dict[str, str]:
    """
    Return display name with strength and dosage form for an RxCUI.

    Uses /RxTerms/rxcui/{rxcui}/allinfo.json.
    Returns a dict with keys:
        display_name    — full display name with strength/form
        strength        — strength string (e.g. "500 MG")
        dose_form       — dose form (e.g. "Oral Tablet")
        full_name       — full generic name
    Returns {} on failure or no match.

    Example
    -------
    get_rxterms("316945") → {"display_name": "Metformin 500 MG Oral Tablet",
                              "strength": "500 MG", ...}
    """
    if not rxcui or not rxcui.strip():
        return {}
    try:
        data = _rxnav_get(f"/RxTerms/rxcui/{rxcui.strip()}/allinfo.json")
        info = data.get("rxtermsProperties", {}) or {}
        if not info:
            return {}
        return {
            "display_name": info.get("displayName", ""),
            "strength":     info.get("strength", ""),
            "dose_form":    info.get("rxtermsDoseForm", ""),
            "full_name":    info.get("fullName", ""),
        }
    except Exception as exc:
        logger.warning("[RxNav] get_rxterms('%s') failed: %s", rxcui, exc)
        return {}


def get_drug_class(rxcui: str) -> list[str]:
    """
    Return therapeutic drug classes for an RxCUI (ATC, VA, MeSH).

    Uses /rxclass/class/byRxcui.json?rxcui={rxcui}.
    Returns a list of class name strings.
    Returns [] on failure or no match.

    Example
    -------
    get_drug_class("161") → ["Analgesics", "Anti-Inflammatory Agents, Non-Steroidal"]
    """
    if not rxcui or not rxcui.strip():
        return []
    try:
        data = _rxnav_get(
            "/rxclass/class/byRxcui.json",
            {"rxcui": rxcui.strip(), "relaSource": "ATC,VA,MESH"},
        )
        rx_classes = data.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []) or []
        seen: set[str] = set()
        classes: list[str] = []
        for item in rx_classes:
            cls_name = item.get("rxclassMinConceptItem", {}).get("className", "")
            if cls_name and cls_name not in seen:
                seen.add(cls_name)
                classes.append(cls_name)
        return classes
    except Exception as exc:
        logger.warning("[RxNav] get_drug_class('%s') failed: %s", rxcui, exc)
        return []


def get_drug_interactions(rxcui1: str, rxcui2: str) -> list[dict]:
    """
    Return vetted drug interaction data from RxNav (DrugBank / ONCHigh sources).

    Uses /interaction/list.json?rxcuis={rxcui1}+{rxcui2}.
    Returns a list of interaction dicts, each with keys:
        severity        — "high", "moderate", "low" (or "" if unknown)
        description     — plain-English description of the interaction
        source          — source name (e.g. "DrugBank")
        drug1           — first drug name
        drug2           — second drug name
    Returns [] on failure, no match, or if either rxcui is empty.

    Example
    -------
    get_drug_interactions("41493", "1000560") →
        [{"severity": "high", "description": "...", "source": "DrugBank", ...}]
    """
    if not rxcui1 or not rxcui1.strip() or not rxcui2 or not rxcui2.strip():
        return []
    try:
        data = _rxnav_get(
            "/interaction/list.json",
            {"rxcuis": f"{rxcui1.strip()} {rxcui2.strip()}"},
        )
        full_interaction = data.get("fullInteractionTypeGroup", []) or []
        interactions: list[dict] = []

        for group in full_interaction:
            source = group.get("sourceName", "")
            for itype in group.get("fullInteractionType", []) or []:
                pair = itype.get("interactionPair", []) or []
                for p in pair:
                    description = p.get("description", "")
                    severity = p.get("severity", "").lower()
                    # Extract drug names from interactionConcept list
                    concepts = p.get("interactionConcept", []) or []
                    d1 = concepts[0].get("minConceptItem", {}).get("name", "") if len(concepts) > 0 else ""
                    d2 = concepts[1].get("minConceptItem", {}).get("name", "") if len(concepts) > 1 else ""
                    if description:
                        interactions.append({
                            "severity":    severity,
                            "description": description[:500],
                            "source":      source,
                            "drug1":       d1,
                            "drug2":       d2,
                        })

        logger.debug(
            "[RxNav] %d interactions for rxcui %s + %s", len(interactions), rxcui1, rxcui2
        )
        return interactions
    except Exception as exc:
        logger.warning(
            "[RxNav] get_drug_interactions('%s', '%s') failed: %s", rxcui1, rxcui2, exc
        )
        return []
