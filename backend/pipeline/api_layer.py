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
DAILYMED_SPL_URL       = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"

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
    # DailyMed SET IDs
    dailymed_setids: dict[str, str]     = field(default_factory=dict)
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



async def fetch_dailymed_setid(drug_name: str) -> str | None:
    """Look up the DailyMed SET ID for a drug to build source URLs."""
    if not drug_name:
        return None
    data = await _async_get(DAILYMED_SPL_URL, params={
        "drug_name": drug_name, "page": "1", "pagesize": "1"
    })
    if not data:
        return None
    results = data.get("data", [])
    if results:
        return results[0].get("setid")
    return None


def parse_structured_side_effects(
    drug_name: str,
    fda_label: dict | None,
    raw_label: dict | None,
    faers_terms: list[str] | None = None,
    dailymed_setid: str | None = None,
) -> dict:
    """
    Parse raw FDA label data into a structured side effects object.

    Each side effect entry is individually tagged with its source section,
    label source, and frequency text (if available). This ensures every
    item shown to the user is traceable to real FDA label data.

    Returns a dict with:
      - drug: canonical_name, brand_names, resolved_from
      - side_effects_structured: { common: [...], serious: [...], boxed_warning: [...] }
      - side_effects (legacy tiered format for backward compat)
      - boxed_warnings (legacy list of strings)
      - mechanism_of_action
      - sources / label_info
    """
    # ── Extract metadata from raw openFDA label ──────────────────────────────
    openfda = (raw_label or {}).get("openfda", {}) if raw_label else {}
    set_ids = openfda.get("set_id", [])
    set_id = set_ids[0] if set_ids else None
    app_nums = openfda.get("application_number", [])
    nda = app_nums[0] if app_nums else None
    pharm_moa = openfda.get("pharm_class_moa", [])
    pharm_epc = openfda.get("pharm_class_epc", [])
    brand_names = openfda.get("brand_name", [])
    generic_names = openfda.get("generic_name", [])

    # Label revision date
    last_updated = ""
    if raw_label:
        eff_date = str(raw_label.get("effective_time", "") or "")
        if eff_date and len(eff_date) >= 8 and eff_date[:8].isdigit():
            last_updated = f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}"

    effective_setid = dailymed_setid or set_id
    label_source = "dailymed" if effective_setid else "openfda"

    # ── Build result skeleton ────────────────────────────────────────────────
    result: dict = {
        "drug_name": drug_name,
        # New structured drug info block
        "drug": {
            "canonical_name": (generic_names[0] if generic_names else drug_name).title(),
            "brand_names": [b.title() for b in brand_names[:5]],
            "resolved_from": drug_name,
        },
        "generic_name": generic_names[0] if generic_names else drug_name,
        "brand_names": [b.title() for b in brand_names[:5]],
        # New per-item structured side effects (each item is a dict with source info)
        "side_effects_structured": {
            "common": [],
            "serious": [],
            "boxed_warning": [],
        },
        # Legacy tiered format (kept for frontend backward compatibility)
        "side_effects": {
            "very_common": {"label": "Very Common (>10%)", "items": []},
            "common":      {"label": "Common (1-10%)",    "items": []},
            "uncommon":    {"label": "Uncommon (<1%)",     "items": []},
            "serious":     {"label": "Serious — Seek Immediate Medical Attention", "items": [], "urgent": True},
        },
        "boxed_warnings": [],
        "mechanism_of_action": {
            "summary": "",
            "pharmacologic_class": "",
            "molecular_targets": [],
            "detail": "",
        },
        "sources": [],
        # New label_info block for source citation
        "label_info": {
            "source": label_source,
            "revision_date": last_updated,
            "source_url": (
                f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={effective_setid}"
                if effective_setid
                else "https://dailymed.nlm.nih.gov/dailymed/"
            ),
        },
        "disclaimer": (
            "This information is from FDA drug labels and is not medical advice. "
            "For emergencies, call 911."
        ),
    }

    if not fda_label and not raw_label:
        return result

    # ── Build source URLs (legacy format for frontend) ───────────────────────
    if effective_setid:
        result["sources"].append({
            "id": 1,
            "name": f"DailyMed — {drug_name.title()} label",
            "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={effective_setid}",
            "section": "ADVERSE REACTIONS",
            "last_updated": last_updated,
        })
    if nda:
        result["sources"].append({
            "id": 2,
            "name": "Drugs@FDA Application",
            "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={nda}",
            "section": "FDA Label",
            "last_updated": last_updated,
        })
    result["sources"].append({
        "id": len(result["sources"]) + 1,
        "name": "openFDA Drug Label API",
        "url": f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{drug_name}"&limit=1',
        "section": "adverse_reactions",
        "last_updated": last_updated,
    })

    # ── Boxed warnings — highest severity ────────────────────────────────────
    boxed_text = (fda_label or {}).get("boxed_warning", "")
    if boxed_text:
        boxed_sentences = [s.strip() for s in boxed_text.split(".") if len(s.strip()) > 10][:3]
        result["boxed_warnings"] = boxed_sentences  # legacy
        for sentence in boxed_sentences:
            result["side_effects_structured"]["boxed_warning"].append({
                "term": sentence,
                "seriousness": "boxed_warning",
                "frequency_text": None,
                "source_section": "Boxed Warning",
                "label_source": label_source,
                "last_updated": last_updated,
                "ai_generated": False,
            })

    # ── Mechanism of action ──────────────────────────────────────────────────
    clin_pharm = (fda_label or {}).get("clinical_pharmacology", "")
    description = (fda_label or {}).get("description", "")
    moa_class = pharm_moa[0] if pharm_moa else ""
    epc_class = pharm_epc[0] if pharm_epc else ""

    result["mechanism_of_action"]["pharmacologic_class"] = epc_class or moa_class
    if moa_class:
        result["mechanism_of_action"]["molecular_targets"] = [moa_class.replace(" [MoA]", "")]

    if clin_pharm:
        sentences = [s.strip() + "." for s in clin_pharm.split(".") if len(s.strip()) > 15]
        result["mechanism_of_action"]["summary"] = " ".join(sentences[:2])[:300]
        result["mechanism_of_action"]["detail"] = " ".join(sentences[:5])[:800]
    elif description:
        sentences = [s.strip() + "." for s in description.split(".") if len(s.strip()) > 15]
        result["mechanism_of_action"]["summary"] = " ".join(sentences[:2])[:300]
        result["mechanism_of_action"]["detail"] = " ".join(sentences[:4])[:600]

    # ── Parse adverse reactions into per-item entries ─────────────────────────
    adverse_text = (fda_label or {}).get("adverse_reactions", "")
    warnings_text = (fda_label or {}).get("warnings", "")

    # Parse adverse reactions text into structured entries with source tagging
    _extract_tagged_effects(
        text=adverse_text,
        source_section="Adverse Reactions",
        label_source=label_source,
        last_updated=last_updated,
        structured=result["side_effects_structured"],
        legacy_tiers=result["side_effects"],
    )

    # Parse warnings text into serious effects with source tagging
    if warnings_text:
        _extract_serious_from_warnings_tagged(
            text=warnings_text,
            label_source=label_source,
            last_updated=last_updated,
            structured=result["side_effects_structured"],
            legacy_serious=result["side_effects"]["serious"]["items"],
        )

    # ── Merge FAERS terms into common if label text didn't yield enough ──────
    if faers_terms:
        existing = set()
        for cat in result["side_effects_structured"].values():
            for item in cat:
                if isinstance(item, dict) and "term" in item:
                    existing.add(item["term"].lower())
        # Also check legacy tiers
        for tier in result["side_effects"].values():
            existing.update(s.lower() for s in tier.get("items", []))

        for term in faers_terms:
            clean = term.strip().lower()
            if clean and clean not in existing and len(clean) > 2:
                # FAERS terms are real data from FDA Adverse Event Reporting System
                result["side_effects_structured"]["common"].append({
                    "term": clean.title(),
                    "seriousness": "common",
                    "frequency_text": None,  # FAERS doesn't give per-label frequency
                    "source_section": "FAERS Adverse Events",
                    "label_source": "fda_faers",
                    "last_updated": last_updated,
                    "ai_generated": False,
                })
                result["side_effects"]["common"]["items"].append(clean.title())
                existing.add(clean)
                if len(result["side_effects_structured"]["common"]) >= 12:
                    break

    # Cap legacy tiers at 10 items each
    for key in result["side_effects"]:
        result["side_effects"][key]["items"] = result["side_effects"][key]["items"][:10]

    return result


def _extract_tagged_effects(
    text: str,
    source_section: str,
    label_source: str,
    last_updated: str,
    structured: dict,
    legacy_tiers: dict,
) -> None:
    """
    Parse FDA adverse reactions text into individually-tagged side effect entries.

    Each extracted term gets tagged with its source_section, label_source,
    frequency_text (if parseable), and seriousness classification.
    """
    if not text:
        return

    text_lower = text.lower()

    # Remove HTML-like tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Split by common delimiters found in FDA labels
    parts = re.split(r"[,;•·\n]+", clean)
    effects = []
    for p in parts:
        t = p.strip()
        # Remove leading dashes, bullets, or numbering
        t = re.sub(r"^[-–—\*\d\.\)]+\s*", "", t).strip()
        if 3 < len(t) < 60 and t and not t[0].isdigit():
            # Skip headers, table labels, and meta-text
            if any(skip in t.lower() for skip in [
                "adverse reaction", "table ", "the following",
                "section", "clinical trial", "placebo",
                "incidence", "reported", "patients",
            ]):
                continue
            effects.append(t)

    # Keywords for classifying severity and frequency
    very_common_kw = ["most common", ">10%", "≥10%", "very common", "most frequently"]
    uncommon_kw = ["rare", "<1%", "uncommon", "infrequent", "isolated"]
    serious_kw = [
        "fatal", "life-threatening", "death", "anaphylaxis", "stevens-johnson",
        "hepatic failure", "cardiac arrest", "renal failure", "hemorrhage",
    ]

    seen = set()
    for effect in effects:
        eff_lower = effect.lower()
        if eff_lower in seen:
            continue
        seen.add(eff_lower)

        # Determine frequency text from surrounding context
        frequency_text = _extract_frequency_near(text_lower, eff_lower)

        # Classify seriousness
        if any(kw in eff_lower for kw in serious_kw):
            seriousness = "serious"
            tier_key = "serious"
        elif any(kw in text_lower[:text_lower.find(eff_lower) + 100] for kw in very_common_kw if eff_lower in text_lower):
            seriousness = "common"
            tier_key = "very_common"
            if not frequency_text:
                frequency_text = "reported in >10% of patients"
        elif any(kw in text_lower[:text_lower.find(eff_lower) + 100] for kw in uncommon_kw if eff_lower in text_lower):
            seriousness = "common"
            tier_key = "uncommon"
            if not frequency_text:
                frequency_text = "reported in <1% of patients"
        else:
            seriousness = "common"
            tier_key = "common"

        # Add to new structured format (per-item source-tagged)
        category = "serious" if seriousness == "serious" else "common"
        structured[category].append({
            "term": effect,
            "seriousness": seriousness,
            "frequency_text": frequency_text,
            "source_section": source_section,
            "label_source": label_source,
            "last_updated": last_updated,
            "ai_generated": False,
        })

        # Also add to legacy tiered format for backward compat
        if effect not in legacy_tiers[tier_key]["items"]:
            legacy_tiers[tier_key]["items"].append(effect)

    # Cap structured entries
    structured["common"] = structured["common"][:12]
    structured["serious"] = structured["serious"][:8]


def _extract_frequency_near(text_lower: str, term_lower: str) -> str | None:
    """
    Try to find a frequency statement near the term in the label text.

    Looks for patterns like "10%", "1 in 100", "commonly reported", etc.
    Returns the frequency text if found, or None.
    """
    idx = text_lower.find(term_lower)
    if idx == -1:
        return None

    # Check a window around the term (200 chars before and after)
    start = max(0, idx - 200)
    end = min(len(text_lower), idx + len(term_lower) + 200)
    window = text_lower[start:end]

    # Look for percentage patterns
    pct_match = re.search(r"(\d+\.?\d*)\s*%", window)
    if pct_match:
        pct = float(pct_match.group(1))
        if pct > 10:
            return f"reported in >{int(pct)}% of patients"
        elif pct > 0:
            return f"reported in ~{pct_match.group(1)}% of patients"

    # Look for frequency words
    for kw, label in [
        ("most common", "commonly reported"),
        ("very common", "very commonly reported"),
        ("common", "commonly reported"),
        ("uncommon", "uncommonly reported"),
        ("rare", "rarely reported"),
        ("frequently", "frequently reported"),
    ]:
        if kw in window:
            return label

    return None


def _extract_serious_from_warnings_tagged(
    text: str,
    label_source: str,
    last_updated: str,
    structured: dict,
    legacy_serious: list,
) -> None:
    """
    Extract serious/life-threatening effects from the warnings section.

    Each extracted term is tagged with source_section = "Warnings and Precautions".
    """
    serious_kw = [
        "fatal", "death", "life-threatening", "anaphyla", "stevens-johnson",
        "hepatic failure", "renal failure", "hemorrhag", "cardiac",
        "suicidal", "stroke", "heart attack", "gi bleed", "perforation",
    ]
    sentences = re.split(r"[.;]", text)
    existing = set()
    for item in structured.get("serious", []):
        if isinstance(item, dict):
            existing.add(item["term"].lower())
    existing.update(s.lower() for s in legacy_serious)

    for sent in sentences:
        clean = sent.strip()
        if any(kw in clean.lower() for kw in serious_kw) and len(clean) > 10:
            short = clean[:100]
            if short.lower() not in existing:
                # Add to new structured format
                structured["serious"].append({
                    "term": short,
                    "seriousness": "serious",
                    "frequency_text": None,
                    "source_section": "Warnings and Precautions",
                    "label_source": label_source,
                    "last_updated": last_updated,
                    "ai_generated": False,
                })
                # Add to legacy format
                legacy_serious.append(short)
                existing.add(short.lower())

    # Cap at 6 items
    legacy_serious[:] = legacy_serious[:6]
    structured["serious"] = structured["serious"][:8]


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

    # DailyMed SET ID for primary drug (for source URL building)
    tasks["dailymed"] = fetch_dailymed_setid(primary_drug)

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

    # DailyMed SET ID
    dm = fetched.get("dailymed")
    if dm and not isinstance(dm, Exception):
        results.dailymed_setids[primary_drug] = dm
        if "DailyMed" not in results.sources_used:
            results.sources_used.append("DailyMed")

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
