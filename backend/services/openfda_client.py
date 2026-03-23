"""
RxBuddy Services — OpenFDA API Client
======================================

Thin wrapper around the FDA openFDA REST API.
No API key required (but optional via OPENFDA_API_KEY env var for higher rate limits).

Functions
---------
get_adverse_events(drug_name)  → FAERS reaction MedDRA terms (list of strings)
get_fda_label(drug_name)       → SPL label sections dict
get_ndc_info(drug_name)        → product info dict (generic_name, brand_name, etc.)
get_recalls(drug_name)         → list of recall dicts
get_shortage(drug_name)        → shortage status dict (empty if no shortage data)

All functions:
  - Return empty dict / empty list on any failure (never raise)
  - Accept OPENFDA_API_KEY from env for optional rate-limit exemption
  - Enforce a 5-second timeout on every network call
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger("rxbuddy.services.openfda")

_BASE = "https://api.fda.gov/drug"
_TIMEOUT = 5  # seconds — hard cap per call


def _api_key_param() -> dict[str, str]:
    """Return {"api_key": key} if OPENFDA_API_KEY is set, else empty dict."""
    key = os.getenv("OPENFDA_API_KEY", "").strip()
    return {"api_key": key} if key else {}


def _get(url: str, params: dict[str, Any]) -> dict:
    """GET with timeout + User-Agent; returns parsed JSON or {} on error."""
    try:
        resp = requests.get(
            url,
            params={**params, **_api_key_param()},
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("[openFDA] GET %s failed: %s", url, exc)
        return {}


# ── 1. Adverse Events (FAERS) ─────────────────────────────────────────────────

def get_adverse_events(drug_name: str) -> list[str]:
    """
    Return the top adverse event reaction terms from FDA FAERS for a drug.

    Uses the /drug/event.json endpoint, counting MedDRA reaction terms.
    Returns a list of reaction term strings (e.g. ["nausea", "headache", ...]).
    Returns [] on failure or no data.

    Example
    -------
    get_adverse_events("metformin") → ["nausea", "diarrhoea", "vomiting", ...]
    """
    if not drug_name or not drug_name.strip():
        return []
    try:
        name = drug_name.strip().lower()
        data = _get(
            f"{_BASE}/event.json",
            {
                "search": f'patient.drug.medicinalproduct:"{name}"',
                "count": "patient.reaction.reactionmeddrapt.exact",
                "limit": "15",
            },
        )
        results = data.get("results", [])
        terms = [r.get("term", "") for r in results if r.get("term")]
        logger.debug("[openFDA/FAERS] %d reactions for '%s'", len(terms), drug_name)
        return terms
    except Exception as exc:
        logger.warning("[openFDA/FAERS] get_adverse_events('%s') failed: %s", drug_name, exc)
        return []


# ── 2. FDA Label ──────────────────────────────────────────────────────────────

def get_fda_label(drug_name: str) -> dict[str, str]:
    """
    Return selected SPL label sections for a drug.

    Tries generic_name, then brand_name, then substance_name.
    Returns a dict with keys:
        adverse_reactions, warnings, contraindications, drug_interactions,
        dosage_and_administration, pregnancy, nursing_mothers, boxed_warning,
        indications_and_usage
    All values are strings (first 1000 chars of the section; "" if absent).
    Returns {} on failure.

    Example
    -------
    get_fda_label("metformin") → {"adverse_reactions": "...", "warnings": "...", ...}
    """
    if not drug_name or not drug_name.strip():
        return {}

    name = drug_name.strip().lower()
    _SECTIONS = [
        "adverse_reactions",
        "warnings",
        "contraindications",
        "drug_interactions",
        "dosage_and_administration",
        "pregnancy",
        "nursing_mothers",
        "boxed_warning",
        "indications_and_usage",
    ]

    for field in ("generic_name", "brand_name", "substance_name"):
        data = _get(
            f"{_BASE}/label.json",
            {"search": f'openfda.{field}:"{name}"', "limit": "1"},
        )
        results = data.get("results", [])
        if not results:
            continue

        raw = results[0]

        def _sec(k: str) -> str:
            val = raw.get(k, [])
            return val[0][:1000] if isinstance(val, list) and val else ""

        label = {k: _sec(k) for k in _SECTIONS}
        has_data = any(v for v in label.values())
        if has_data:
            logger.debug("[openFDA/label] Found label for '%s' via %s", drug_name, field)
            return label

    logger.debug("[openFDA/label] No label found for '%s'", drug_name)
    return {}


# ── 3. NDC Product Info ───────────────────────────────────────────────────────

def get_ndc_info(drug_name: str) -> dict[str, str]:
    """
    Return basic NDC product information for a drug.

    Queries the /drug/ndc.json endpoint.
    Returns a dict with keys:
        generic_name, brand_name, dosage_form, route,
        dea_schedule, product_type, marketing_status
    All values are strings ("" if absent).
    Returns {} on failure or no match.

    Example
    -------
    get_ndc_info("metformin") → {"generic_name": "metformin hydrochloride",
                                  "brand_name": "GLUCOPHAGE", "dosage_form": "TABLET", ...}
    """
    if not drug_name or not drug_name.strip():
        return {}

    name = drug_name.strip().lower()

    for field in ("generic_name", "brand_name"):
        data = _get(
            f"{_BASE}/ndc.json",
            {"search": f'{field}:"{name}"', "limit": "1"},
        )
        results = data.get("results", [])
        if not results:
            continue

        r = results[0]
        brand = r.get("brand_name", "") or ""
        if isinstance(brand, list):
            brand = brand[0] if brand else ""

        route = r.get("route", [])
        if isinstance(route, list):
            route = route[0] if route else ""

        info = {
            "generic_name":      (r.get("generic_name") or "").lower(),
            "brand_name":        brand,
            "dosage_form":       r.get("dosage_form", ""),
            "route":             route,
            "dea_schedule":      r.get("dea_schedule", ""),
            "product_type":      r.get("product_type", ""),
            "marketing_status":  r.get("marketing_category", ""),
        }
        has_data = any(v for v in info.values())
        if has_data:
            logger.debug("[openFDA/ndc] Found NDC info for '%s' via %s", drug_name, field)
            return info

    logger.debug("[openFDA/ndc] No NDC info for '%s'", drug_name)
    return {}


# ── 4. Drug Recalls ───────────────────────────────────────────────────────────

def get_recalls(drug_name: str) -> list[dict[str, str]]:
    """
    Return active drug recalls for a drug name.

    Queries the /drug/enforcement.json endpoint.
    Each item has keys:
        recall_number, reason_for_recall, classification, status,
        product_description, recalling_firm, voluntary_mandated
    Returns [] on failure or no recalls.
    Only returns Class I and II recalls (Class III and voluntary market withdrawals filtered out).

    Example
    -------
    get_recalls("valsartan") → [{"recall_number": "D-1234-...", "classification": "Class I", ...}]
    """
    if not drug_name or not drug_name.strip():
        return []

    name = drug_name.strip().lower()

    try:
        data = _get(
            f"{_BASE}/enforcement.json",
            {
                "search": f'product_description:"{name}" AND status:"Ongoing"',
                "limit": "5",
            },
        )
        results = data.get("results", [])

        recalls = []
        for r in results:
            cls = r.get("classification", "")
            # Only return Class I and Class II recalls (highest severity)
            if cls not in ("Class I", "Class II"):
                continue
            recalls.append({
                "recall_number":      r.get("recall_number", ""),
                "reason_for_recall":  r.get("reason_for_recall", "")[:300],
                "classification":     cls,
                "status":             r.get("status", ""),
                "product_description": r.get("product_description", "")[:200],
                "recalling_firm":     r.get("recalling_firm", ""),
                "voluntary_mandated": r.get("voluntary_mandated", ""),
            })

        logger.debug("[openFDA/enforcement] %d Class I/II recalls for '%s'", len(recalls), drug_name)
        return recalls
    except Exception as exc:
        logger.warning("[openFDA/enforcement] get_recalls('%s') failed: %s", drug_name, exc)
        return []


# ── 5. Drug Shortage ──────────────────────────────────────────────────────────

def get_shortage(drug_name: str) -> dict[str, str]:
    """
    Return shortage status for a drug if available in FDA enforcement data.

    Note: The FDA does not have a dedicated shortage API endpoint in openFDA.
    This function checks whether there are any active enforcement actions with
    "shortage" in the reason_for_recall field as a proxy indicator.

    Returns a dict with keys:
        status ("shortage_indicator_found" | "no_shortage_data"),
        detail (string with context, may be "")
    Always returns a dict — never raises.

    Example
    -------
    get_shortage("amoxicillin") → {"status": "no_shortage_data", "detail": ""}
    """
    if not drug_name or not drug_name.strip():
        return {"status": "no_shortage_data", "detail": ""}

    name = drug_name.strip().lower()

    try:
        data = _get(
            f"{_BASE}/enforcement.json",
            {
                "search": f'product_description:"{name}" AND reason_for_recall:"shortage"',
                "limit": "1",
            },
        )
        results = data.get("results", [])
        if results:
            reason = results[0].get("reason_for_recall", "")[:200]
            logger.debug("[openFDA/enforcement] Shortage indicator found for '%s'", drug_name)
            return {"status": "shortage_indicator_found", "detail": reason}
        return {"status": "no_shortage_data", "detail": ""}
    except Exception as exc:
        logger.warning("[openFDA/enforcement] get_shortage('%s') failed: %s", drug_name, exc)
        return {"status": "no_shortage_data", "detail": ""}
