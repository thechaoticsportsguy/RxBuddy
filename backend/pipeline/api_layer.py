"""
Pipeline Step 4 — Async Parallel API Fetching.

Fetches data from multiple medical APIs concurrently using aiohttp.
Each request has a 1.5-second timeout. If any single API fails, the
others still succeed — graceful degradation is built in.

APIs fetched in parallel:
  - OpenFDA drug labels   (warnings, interactions, dosing, etc.)
  - RxNorm                (drug name normalization, RxCUI lookup)
  - MedlinePlus           (patient-friendly summaries)
  - openFDA FAERS         (adverse event reports)
  - openFDA Enforcement   (active recalls)
  - RxNav interactions    (DrugBank vetted interactions)

Falls back to synchronous requests if aiohttp is not installed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("rxbuddy.pipeline.api_layer")

# Ensure backend/ is on the path
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── API endpoints ─────────────────────────────────────────────────────────────
OPENFDA_LABEL_URL      = "https://api.fda.gov/drug/label.json"
OPENFDA_EVENT_URL      = "https://api.fda.gov/drug/event.json"
OPENFDA_ENFORCE_URL    = "https://api.fda.gov/drug/enforcement.json"
RXNORM_RXCUI_URL       = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
RXNAV_INTERACT_URL     = "https://rxnav.nlm.nih.gov/REST/interaction/list.json"
MEDLINEPLUS_URL        = "https://connect.medlineplus.gov/application"

API_TIMEOUT = 1.5   # seconds — hard timeout per request
HEADERS     = {"User-Agent": "RxBuddy/2.0"}


# ── Result container ──────────────────────────────────────────────────────────
@dataclass
class APIResults:
    """Holds all data fetched from external APIs for a query."""
    # FDA label data (parsed sections)
    fda_labels: dict[str, dict]         = field(default_factory=dict)
    # Raw OpenFDA label results (for metadata extraction)
    fda_raw_labels: dict[str, dict]     = field(default_factory=dict)
    # MedlinePlus patient summaries
    medlineplus: dict[str, dict]        = field(default_factory=dict)
    # FAERS adverse events (top reaction terms)
    adverse_events: dict[str, list]     = field(default_factory=dict)
    # Active FDA recalls
    recalls: dict[str, list]            = field(default_factory=dict)
    # RxNav vetted interactions
    rxnav_interactions: list[dict]      = field(default_factory=list)
    # RxCUI lookups
    rxcuis: dict[str, str]              = field(default_factory=dict)
    # Which sources returned data
    sources_used: list[str]             = field(default_factory=list)
    # Errors encountered (logged, not raised)
    errors: list[str]                   = field(default_factory=list)


# ── Async fetchers ────────────────────────────────────────────────────────────
# We try to use aiohttp for true async. If not installed, fall back to
# synchronous requests wrapped in asyncio.to_thread().

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False
    import requests as _sync_requests
    logger.warning("[APILayer] aiohttp not installed — falling back to sync requests")


async def _async_get(url: str, params: dict | None = None) -> dict | None:
    """GET a URL and return parsed JSON, or None on failure. 1.5s timeout."""
    if _HAS_AIOHTTP:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 404:
                        return None
                    resp.raise_for_status()
                    return await resp.json()
        except Exception as exc:
            logger.debug("[APILayer] aiohttp GET %s failed: %s", url, exc)
            return None
    else:
        # Synchronous fallback — run in thread to not block event loop
        def _sync():
            try:
                r = _sync_requests.get(url, params=params, headers=HEADERS, timeout=API_TIMEOUT)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                logger.debug("[APILayer] sync GET %s failed: %s", url, exc)
                return None
        return await asyncio.to_thread(_sync)


# ── Individual API fetchers ───────────────────────────────────────────────────

async def fetch_fda_label(drug_name: str) -> tuple[dict | None, dict | None]:
    """
    Fetch FDA label for a drug. Tries generic_name, brand_name, substance_name.
    Returns (parsed_sections, raw_label) or (None, None).
    """
    if not drug_name:
        return None, None

    # Try multiple search strategies
    for fld in ("generic_name", "brand_name", "substance_name"):
        url = f'{OPENFDA_LABEL_URL}?search=openfda.{fld}:"{drug_name}"&limit=1'
        data = await _async_get(url)
        if not data:
            continue

        results = data.get("results", [])
        if not results:
            continue

        raw_label = results[0]

        def get_section(k: str) -> str:
            val = raw_label.get(k, [])
            return val[0][:1200] if isinstance(val, list) and val else ""

        parsed = {
            "drug_name":                  drug_name,
            "warnings":                   get_section("warnings"),
            "boxed_warning":              get_section("boxed_warning"),
            "dosage_and_administration":  get_section("dosage_and_administration"),
            "contraindications":          get_section("contraindications"),
            "drug_interactions":          get_section("drug_interactions"),
            "adverse_reactions":          get_section("adverse_reactions"),
            "pregnancy":                  get_section("pregnancy"),
            "lactation":                  get_section("lactation"),
            "indications_and_usage":      get_section("indications_and_usage"),
            "use_in_specific_populations": get_section("use_in_specific_populations"),
            "clinical_pharmacology":      get_section("clinical_pharmacology"),
            "description":                get_section("description"),
        }

        has_data = any(v for k, v in parsed.items() if k != "drug_name" and v)
        if has_data:
            logger.info("[FDA] Resolved '%s' via openfda.%s", drug_name, fld)
            return parsed, raw_label

    # Also try drug_catalog brand names
    try:
        from drug_catalog import find_drug
        rec = find_drug(drug_name)
        if rec:
            # Try canonical name
            if rec.canonical_name.lower() != drug_name.lower():
                result = await fetch_fda_label(rec.canonical_name)
                if result[0]:
                    return result
            # Try first brand name
            for brand in rec.brand_names[:2]:
                if brand.lower() != drug_name.lower():
                    url = f'{OPENFDA_LABEL_URL}?search=openfda.brand_name:"{brand}"&limit=1'
                    data = await _async_get(url)
                    if data and data.get("results"):
                        raw_label = data["results"][0]

                        def get_section(k: str) -> str:
                            val = raw_label.get(k, [])
                            return val[0][:1200] if isinstance(val, list) and val else ""

                        parsed = {
                            "drug_name": drug_name,
                            "warnings": get_section("warnings"),
                            "boxed_warning": get_section("boxed_warning"),
                            "dosage_and_administration": get_section("dosage_and_administration"),
                            "contraindications": get_section("contraindications"),
                            "drug_interactions": get_section("drug_interactions"),
                            "adverse_reactions": get_section("adverse_reactions"),
                            "pregnancy": get_section("pregnancy"),
                            "lactation": get_section("lactation"),
                            "indications_and_usage": get_section("indications_and_usage"),
                            "use_in_specific_populations": get_section("use_in_specific_populations"),
                            "clinical_pharmacology": get_section("clinical_pharmacology"),
                            "description": get_section("description"),
                        }
                        if any(v for k, v in parsed.items() if k != "drug_name" and v):
                            return parsed, raw_label
    except Exception:
        pass

    logger.info("[FDA] No label found for '%s'", drug_name)
    return None, None


async def fetch_rxcui(drug_name: str) -> str | None:
    """Look up RxCUI for a drug name via RxNorm API."""
    if not drug_name:
        return None
    data = await _async_get(RXNORM_RXCUI_URL, params={
        "name": drug_name, "allSourcesFlag": "0"
    })
    if not data:
        return None
    rxcui = data.get("idGroup", {}).get("rxnormId", [None])
    return rxcui[0] if rxcui else None


async def fetch_rxnav_interactions(rxcui_a: str, rxcui_b: str) -> list[dict]:
    """Fetch vetted drug-drug interactions from RxNav (DrugBank source)."""
    if not rxcui_a or not rxcui_b:
        return []
    data = await _async_get(RXNAV_INTERACT_URL, params={
        "rxcuis": f"{rxcui_a}+{rxcui_b}"
    })
    if not data:
        return []

    interactions = []
    for group in data.get("fullInteractionTypeGroup", []):
        for itype in group.get("fullInteractionType", []):
            for pair in itype.get("interactionPair", []):
                interactions.append({
                    "severity": pair.get("severity", ""),
                    "description": pair.get("description", ""),
                    "source": group.get("sourceName", ""),
                    "drug1": itype.get("minConcept", [{}])[0].get("name", "") if itype.get("minConcept") else "",
                    "drug2": itype.get("minConcept", [{}])[1].get("name", "") if len(itype.get("minConcept", [])) > 1 else "",
                })
    return interactions


async def fetch_medlineplus(drug_name: str) -> dict | None:
    """Fetch patient-friendly summary from MedlinePlus Connect."""
    rxcui = await fetch_rxcui(drug_name)
    if not rxcui:
        return None

    data = await _async_get(MEDLINEPLUS_URL, params={
        "mainSearchCriteria.v.cs": "2.16.840.1.113883.6.88",
        "mainSearchCriteria.v.c": rxcui,
        "knowledgeResponseType": "application/json",
    })
    if not data:
        return None

    entries = data.get("feed", {}).get("entry", [])
    if not entries:
        return None

    entry = entries[0]
    summary = entry.get("summary", {}).get("_value", "")
    title = entry.get("title", {}).get("_value", "")
    content = entry.get("content", {}).get("_value", "")
    clean = re.sub(r"<[^>]+>", " ", content)
    clean = re.sub(r"\s+", " ", clean).strip()

    result = {
        "summary": (summary or title)[:300],
        "usage": clean[:300] if clean else "",
        "side_effects": "",
    }

    # Pull side-effects snippet if present
    se_idx = clean.lower().find("side effect")
    if se_idx != -1:
        result["side_effects"] = clean[se_idx:se_idx + 250]

    if any(result[k] for k in ("summary", "usage", "side_effects")):
        return result
    return None


async def fetch_adverse_events(drug_name: str) -> list[str]:
    """Fetch top adverse event terms from FAERS via openFDA."""
    if not drug_name:
        return []
    data = await _async_get(OPENFDA_EVENT_URL, params={
        "search": f'patient.drug.openfda.generic_name:"{drug_name}"',
        "count": "patient.reaction.reactionmeddrapt.exact",
        "limit": "15",
    })
    if not data:
        return []
    results = data.get("results", [])
    return [r.get("term", "") for r in results[:12] if r.get("term")]


async def fetch_recalls(drug_name: str) -> list[dict]:
    """Fetch active Class I/II FDA recalls for a drug."""
    if not drug_name:
        return []
    data = await _async_get(OPENFDA_ENFORCE_URL, params={
        "search": f'openfda.generic_name:"{drug_name}"+AND+(classification:"Class I"+OR+classification:"Class II")',
        "limit": "3",
    })
    if not data:
        return []
    return [
        {
            "classification": r.get("classification", ""),
            "reason_for_recall": r.get("reason_for_recall", "")[:200],
            "product_description": r.get("product_description", "")[:100],
        }
        for r in data.get("results", [])[:2]
    ]


# ── Main parallel fetch orchestrator ─────────────────────────────────────────

async def fetch_all(
    drug_names: list[str],
    intent: str = "general",
) -> APIResults:
    """
    Fetch all external API data for the given drugs — in parallel.

    This is the main entry point for the API layer. It dispatches all
    network calls concurrently and returns an APIResults dataclass.

    Parameters
    ----------
    drug_names : list of canonical generic drug names
    intent     : classified intent string (affects which APIs are queried)

    Returns
    -------
    APIResults with all available data. Missing data = empty defaults.
    """
    results = APIResults()
    if not drug_names:
        return results

    primary_drug = drug_names[0]

    # Build list of async tasks
    tasks = {}

    # FDA labels for ALL drugs (needed for cross-referencing interactions)
    for drug in drug_names:
        tasks[f"fda_{drug}"] = fetch_fda_label(drug)

    # MedlinePlus for primary drug only
    tasks["medlineplus"] = fetch_medlineplus(primary_drug)

    # FAERS adverse events for primary drug
    tasks["adverse_events"] = fetch_adverse_events(primary_drug)

    # Recalls for primary drug
    tasks["recalls"] = fetch_recalls(primary_drug)

    # RxNav interactions (only for interaction intent with 2+ drugs)
    if intent == "interaction" and len(drug_names) >= 2:
        # Need RxCUIs first — fetch them, then interactions
        tasks["rxcui_0"] = fetch_rxcui(drug_names[0])
        tasks["rxcui_1"] = fetch_rxcui(drug_names[1])

    # Execute all tasks concurrently
    keys = list(tasks.keys())
    coros = list(tasks.values())

    try:
        gathered = await asyncio.gather(*coros, return_exceptions=True)
    except Exception as exc:
        logger.error("[APILayer] asyncio.gather failed: %s", exc)
        results.errors.append(str(exc))
        return results

    # Unpack results
    fetched = dict(zip(keys, gathered))

    for drug in drug_names:
        key = f"fda_{drug}"
        val = fetched.get(key)
        if isinstance(val, Exception):
            results.errors.append(f"FDA label {drug}: {val}")
            continue
        if val and isinstance(val, tuple) and val[0]:
            fda_data, raw_label = val
            results.fda_labels[drug] = fda_data
            results.fda_raw_labels[drug] = raw_label
            if "FDA Label" not in results.sources_used:
                results.sources_used.append("FDA Label")

    # MedlinePlus
    ml = fetched.get("medlineplus")
    if ml and not isinstance(ml, Exception) and ml:
        results.medlineplus[primary_drug] = ml
        results.sources_used.append("MedlinePlus")

    # Adverse events
    ae = fetched.get("adverse_events")
    if ae and not isinstance(ae, Exception) and ae:
        results.adverse_events[primary_drug] = ae
        results.sources_used.append("FDA FAERS")

    # Recalls
    rec = fetched.get("recalls")
    if rec and not isinstance(rec, Exception) and rec:
        results.recalls[primary_drug] = rec
        results.sources_used.append("FDA Enforcement")

    # RxCUIs + interactions
    rxcui_0 = fetched.get("rxcui_0")
    rxcui_1 = fetched.get("rxcui_1")
    if (rxcui_0 and not isinstance(rxcui_0, Exception)
            and rxcui_1 and not isinstance(rxcui_1, Exception)):
        results.rxcuis[drug_names[0]] = rxcui_0
        results.rxcuis[drug_names[1]] = rxcui_1
        # Now fetch interactions (this is a follow-up call)
        try:
            ixns = await fetch_rxnav_interactions(rxcui_0, rxcui_1)
            if ixns:
                results.rxnav_interactions = ixns
                results.sources_used.append("RxNav/DrugBank")
        except Exception as exc:
            results.errors.append(f"RxNav interactions: {exc}")

    return results
