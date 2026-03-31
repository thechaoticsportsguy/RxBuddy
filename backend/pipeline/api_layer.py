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

# ── FAERS blocklist — terms that must never appear in the "common" bucket ──────
# FAERS is a voluntary adverse-event reporting database biased toward serious /
# fatal events.  These terms are fine in the "serious" tier but would be
# dangerously misleading if listed as common (1-10%) side effects.
_FAERS_COMMON_BLOCKLIST = frozenset([
    "death", "died", "fatal", "fatality",
    "cardiac arrest", "respiratory arrest", "cardiorespiratory arrest",
    "respiratory failure", "respiratory depression",
    "cardiac failure", "heart failure", "congestive heart failure",
    "circulatory collapse", "circulatory depression",
    "anaphylaxis", "anaphylactic shock", "anaphylactic reaction",
    "overdose", "intentional overdose", "accidental overdose",
    "addiction", "dependence", "drug dependence", "drug abuse",
    "substance abuse", "opioid addiction",
    "coma", "loss of consciousness",
    "acute kidney injury", "renal failure", "hepatic failure", "liver failure",
    "stroke", "cerebrovascular accident",
    "pulmonary embolism", "pulmonary oedema",
    "stevens-johnson", "toxic epidermal necrolysis",
    "off-label use", "drug ineffective",
])


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
            "drug_name":                   drug_name,
            "warnings":                    get_section("warnings"),
            "warnings_and_precautions":    get_section("warnings_and_precautions"),
            "boxed_warning":               get_section("boxed_warning"),
            "dosage_and_administration":   get_section("dosage_and_administration"),
            "contraindications":           get_section("contraindications"),
            "drug_interactions":           get_section("drug_interactions"),
            "adverse_reactions":           get_section("adverse_reactions"),
            "pregnancy":                   get_section("pregnancy"),
            "lactation":                   get_section("lactation"),
            "indications_and_usage":       get_section("indications_and_usage"),
            "use_in_specific_populations": get_section("use_in_specific_populations"),
            "clinical_pharmacology":       get_section("clinical_pharmacology"),
            "description":                 get_section("description"),
        }

        has_data = any(v for k, v in parsed.items() if k != "drug_name" and v)
        if has_data:
            sections_found = [k for k, v in parsed.items() if k != "drug_name" and v]
            # Bug fix: if the fetched label looks like an injectable/parenteral formulation,
            # try to find an oral formulation instead (e.g. Benadryl IV vs OTC allergy pill).
            indications = parsed.get("indications_and_usage", "").lower()
            description_text = parsed.get("description", "").lower()
            looks_parenteral = any(
                kw in indications or kw in description_text
                for kw in ("injection", "intravenous", "parenteral", "injectable", "iv ")
            )
            if looks_parenteral:
                logger.info("[FDA] '%s' label looks parenteral — retrying with ORAL route filter", drug_name)
                oral_url = (
                    f'{OPENFDA_LABEL_URL}?search=openfda.generic_name:"{drug_name}"'
                    f'+AND+openfda.route:"ORAL"&limit=1'
                )
                oral_data = await _async_get(oral_url)
                if oral_data and oral_data.get("results"):
                    oral_raw = oral_data["results"][0]
                    def get_oral_section(k: str) -> str:  # noqa: E306
                        val = oral_raw.get(k, [])
                        return val[0][:1200] if isinstance(val, list) and val else ""
                    oral_parsed = {
                        "drug_name":                   drug_name,
                        "warnings":                    get_oral_section("warnings"),
                        "warnings_and_precautions":    get_oral_section("warnings_and_precautions"),
                        "boxed_warning":               get_oral_section("boxed_warning"),
                        "dosage_and_administration":   get_oral_section("dosage_and_administration"),
                        "contraindications":           get_oral_section("contraindications"),
                        "drug_interactions":           get_oral_section("drug_interactions"),
                        "adverse_reactions":           get_oral_section("adverse_reactions"),
                        "pregnancy":                   get_oral_section("pregnancy"),
                        "lactation":                   get_oral_section("lactation"),
                        "indications_and_usage":       get_oral_section("indications_and_usage"),
                        "use_in_specific_populations": get_oral_section("use_in_specific_populations"),
                        "clinical_pharmacology":       get_oral_section("clinical_pharmacology"),
                        "description":                 get_oral_section("description"),
                    }
                    if any(v for k, v in oral_parsed.items() if k != "drug_name" and v):
                        logger.info("[FDA] '%s' oral label found — using oral formulation", drug_name)
                        return oral_parsed, oral_raw
            logger.info("[FDA] Label for '%s' via openfda.%s — sections: %s",
                        drug_name, fld, sections_found)
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
                            "drug_name":                   drug_name,
                            "warnings":                    get_section("warnings"),
                            "warnings_and_precautions":    get_section("warnings_and_precautions"),
                            "boxed_warning":               get_section("boxed_warning"),
                            "dosage_and_administration":   get_section("dosage_and_administration"),
                            "contraindications":           get_section("contraindications"),
                            "drug_interactions":           get_section("drug_interactions"),
                            "adverse_reactions":           get_section("adverse_reactions"),
                            "pregnancy":                   get_section("pregnancy"),
                            "lactation":                   get_section("lactation"),
                            "indications_and_usage":       get_section("indications_and_usage"),
                            "use_in_specific_populations": get_section("use_in_specific_populations"),
                            "clinical_pharmacology":       get_section("clinical_pharmacology"),
                            "description":                 get_section("description"),
                        }
                        if any(v for k, v in parsed.items() if k != "drug_name" and v):
                            return parsed, raw_label
    except Exception:
        pass

    logger.info("[FDA] No label found for '%s'", drug_name)
    return None, None


async def fetch_rxcui(drug_name: str) -> str | None:
    """
    Look up RxCUI for a drug name via RxNorm API.

    Tries three strategies in order:
      1. Exact name lookup
      2. Base name (strips common pharmaceutical suffixes like "HCl", "ER")
      3. RxNorm approximate/fuzzy term search
    Logs RxCUI found or not found for every drug.
    """
    if not drug_name:
        return None

    clean = drug_name.strip()

    # Strip common pharmaceutical form/salt suffixes for base variant
    base = re.sub(
        r"\s+(hcl|hydrochloride|sodium|potassium|acetate|succinate|maleate|"
        r"tartrate|citrate|phosphate|sulfate|fumarate|besylate|mesylate|"
        r"xl|er|sr|ir|cr|xr|la)\s*$",
        "", clean, flags=re.IGNORECASE,
    ).strip()

    # Deduplicated list: exact first, then base if different
    variants: list[str] = list(dict.fromkeys(v for v in [clean, base] if v))

    for name in variants:
        data = await _async_get(RXNORM_RXCUI_URL, params={
            "name": name, "allSourcesFlag": "0"
        })
        if data:
            ids = data.get("idGroup", {}).get("rxnormId", [])
            if ids:
                logger.info("[RxNorm] RxCUI for '%s' = %s (via '%s')", drug_name, ids[0], name)
                return ids[0]
        logger.debug("[RxNorm] No exact match for variant '%s'", name)

    # Approximate/fuzzy search as final fallback
    approx = await _async_get(
        "https://rxnav.nlm.nih.gov/REST/approximateTerm.json",
        params={"term": clean, "maxEntries": "1", "option": "0"},
    )
    if approx:
        candidates = (approx.get("approximateGroup") or {}).get("candidate", [])
        if candidates:
            rxcui = candidates[0].get("rxcui")
            if rxcui:
                logger.info("[RxNorm] Approx RxCUI for '%s' = %s", drug_name, rxcui)
                return rxcui

    logger.info("[RxNorm] No RxCUI found for '%s' after %d variant(s) + approx search",
                drug_name, len(variants))
    return None


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
    """
    Look up the DailyMed SET ID for a drug to build source URLs.

    Tries two variants: full name, then first word only.
    Handles empty responses and API timeouts gracefully.
    """
    if not drug_name:
        return None

    clean = drug_name.strip()
    # First word handles compound names like "metformin hcl" → "metformin"
    first_word = clean.split()[0] if clean.split() else clean
    variants = list(dict.fromkeys(v for v in [clean, first_word] if v))

    for name in variants:
        data = await _async_get(DAILYMED_SPL_URL, params={
            "drug_name": name, "page": "1", "pagesize": "1"
        })
        if not data:
            logger.debug("[DailyMed] No response for '%s'", name)
            continue
        results = data.get("data", [])
        if results:
            setid = results[0].get("setid")
            if setid:
                logger.info("[DailyMed] SET ID for '%s' = %s (via '%s')", drug_name, setid, name)
                return setid
        logger.debug("[DailyMed] Empty data for '%s'", name)

    logger.info("[DailyMed] No SET ID found for '%s'", drug_name)
    return None


def parse_structured_side_effects(
    drug_name: str,
    fda_label: dict | None,
    raw_label: dict | None,
    faers_terms: list[str] | None = None,
    dailymed_setid: str | None = None,
) -> dict:
    """
    Return structured side effects for a drug.

    Priority:
      1. Hardcoded clinical fallback  (always clean, highest reliability)
      2. Gemini parse of FDA label    (if GEMINI_API_KEY is set)
      3. FAERS terms only             (filtered through blocklist, max 8)
      4. Empty tier structure         (never runs the broken heuristic parser)

    The old comma-split heuristic (_classify_effects_from_text) is intentionally
    NOT called here — it produces garbage like "And/Or G", "And Coma", and
    "Including Buspirone" by splitting prose paragraphs on commas.
    """
    from pipeline.side_effects_store import _get_class_fallback, _DRUG_ALIASES

    # ── Step 1: hardcoded fallback (most reliable for common drugs) ───────────
    key = drug_name.strip().lower()
    resolved = _DRUG_ALIASES.get(key, key)
    fallback = _get_class_fallback(resolved)
    if fallback:
        logger.info("[Parser] Hardcoded fallback hit for %s (resolved=%s)", drug_name, resolved)
        return fallback

    # ── Step 2: try Gemini parse of the FDA label ─────────────────────────────
    if fda_label or raw_label:
        try:
            from pipeline.side_effects_store import parse_label_with_gemini
            gemini_result = parse_label_with_gemini(drug_name, fda_label or {})
            if gemini_result:
                # Enrich with metadata from raw_label (source URLs, brand names, MOA)
                if raw_label:
                    openfda = raw_label.get("openfda", {})
                    set_ids = openfda.get("set_id", [])
                    set_id = set_ids[0] if set_ids else None
                    app_nums = openfda.get("application_number", [])
                    nda = app_nums[0] if app_nums else None
                    brand_names = openfda.get("brand_name", [])
                    generic_names = openfda.get("generic_name", [])
                    eff_date = raw_label.get("effective_time", "")
                    last_updated = (
                        f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}"
                        if eff_date and len(eff_date) >= 8 else ""
                    )
                    gemini_result["brand_names"] = brand_names[:5]
                    gemini_result["generic_name"] = generic_names[0] if generic_names else drug_name
                    effective_setid = dailymed_setid or set_id
                    sources = []
                    if effective_setid:
                        sources.append({
                            "id": 1, "name": f"DailyMed — {drug_name.title()} label",
                            "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={effective_setid}",
                            "section": "ADVERSE REACTIONS", "last_updated": last_updated,
                        })
                    if nda:
                        sources.append({
                            "id": 2, "name": "Drugs@FDA Application",
                            "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={nda}",
                            "section": "FDA Label", "last_updated": last_updated,
                        })
                    sources.append({
                        "id": len(sources) + 1, "name": "openFDA Drug Label API",
                        "url": f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{drug_name}"&limit=1',
                        "section": "adverse_reactions", "last_updated": last_updated,
                    })
                    gemini_result["sources"] = sources
                logger.info("[Parser] Gemini parse succeeded for %s", drug_name)
                return gemini_result
        except Exception as exc:
            logger.warning("[Parser] Gemini parse error for %s: %s", drug_name, exc)

    # ── Step 3: safe empty structure + filtered FAERS terms only ─────────────
    # DO NOT run the heuristic comma-split parser — it always produces garbage.
    tiers: dict = {
        "very_common": {"label": "Very Common (>10%)",  "items": []},
        "common":      {"label": "Common (1-10%)",       "items": []},
        "uncommon":    {"label": "Uncommon (<1%)",        "items": []},
        "rare":        {"label": "Rare (<0.1%)",          "items": []},
        "serious":     {"label": "Serious — Seek Medical Attention", "items": [], "urgent": True},
    }

    if faers_terms:
        for term in faers_terms[:8]:
            clean = term.strip().lower()
            if clean and len(clean) > 2:
                if not any(blocked in clean for blocked in _FAERS_COMMON_BLOCKLIST):
                    tiers["common"]["items"].append({
                        "display_name": clean.title(),
                        "frequency_category": "common",
                        "confidence_score": 0.3,
                        "severity": "mild",
                        "management": "monitor",
                        "red_flag": False,
                    })

    counts = {tier: len(data["items"]) for tier, data in tiers.items()}
    logger.info("[Parser] %s — FAERS-only fallback, %d effects: %s", drug_name, sum(counts.values()), counts)

    return {
        "drug": drug_name,
        "drug_name": drug_name,
        "side_effects": tiers,
        "boxed_warnings": [],
        "mechanism_of_action": {
            "summary": "", "detail": "", "pharmacologic_class": "", "molecular_targets": [],
        },
        "sources": [],
        "_fallback": True,
        "_fallback_source": "faers_safe",
    }


def _classify_effects_from_text(text: str, tiers: dict) -> None:
    """Parse FDA adverse reactions text and classify into frequency tiers."""
    if not text:
        return

    # Common frequency markers in FDA labels
    text_lower = text.lower()

    # Extract individual effect terms from the text
    # Split by common delimiters: commas, semicolons, bullet patterns
    import re as _re
    # Remove HTML-like tags
    clean = _re.sub(r"<[^>]+>", " ", text)
    # Find terms that look like side effects (short phrases)
    parts = _re.split(r"[,;•·\n]+", clean)
    effects = []
    for p in parts:
        t = p.strip()
        # Remove leading dashes or bullets
        t = _re.sub(r"^[-–—\*\d\.\)]+\s*", "", t).strip()
        if 3 < len(t) < 60 and not t[0].isdigit():
            # Skip lines that are headers or percentages
            if any(skip in t.lower() for skip in ["adverse reaction", "table ", "the following", "section", "clinical trial", "placebo"]):
                continue
            effects.append(t)

    # Classify by frequency keywords in surrounding text
    very_common_kw = ["most common", ">10%", "≥10%", "very common", "most frequently"]
    uncommon_kw = ["rare", "<1%", "uncommon", "infrequent", "isolated"]
    serious_kw = ["fatal", "life-threatening", "death", "anaphylaxis", "stevens-johnson",
                  "hepatic failure", "cardiac arrest", "renal failure", "hemorrhage"]

    for effect in effects:
        eff_lower = effect.lower()
        # Check if this effect matches serious keywords
        if any(kw in eff_lower for kw in serious_kw):
            if effect not in tiers["serious"]["items"]:
                tiers["serious"]["items"].append(effect)
        elif any(kw in text_lower[:text_lower.find(eff_lower) + 100] for kw in very_common_kw if eff_lower in text_lower):
            if effect not in tiers["very_common"]["items"]:
                tiers["very_common"]["items"].append(effect)
        elif any(kw in text_lower[:text_lower.find(eff_lower) + 100] for kw in uncommon_kw if eff_lower in text_lower):
            if effect not in tiers["uncommon"]["items"]:
                tiers["uncommon"]["items"].append(effect)
        else:
            # Don't add blocklisted terms to the common bucket even if
            # they appear in the adverse_reactions section
            if any(blocked in eff_lower for blocked in _FAERS_COMMON_BLOCKLIST):
                continue
            if effect not in tiers["common"]["items"]:
                tiers["common"]["items"].append(effect)

    # Cap each tier
    for key in tiers:
        tiers[key]["items"] = tiers[key]["items"][:10]


def _extract_serious_from_warnings(text: str, serious_items: list) -> None:
    """Extract serious/life-threatening effects from the warnings section."""
    import re as _re
    serious_kw = ["fatal", "death", "life-threatening", "anaphyla", "stevens-johnson",
                  "hepatic failure", "renal failure", "hemorrhag", "cardiac",
                  "suicidal", "stroke", "heart attack", "gi bleed", "perforation"]
    sentences = _re.split(r"[.;]", text)
    existing = set(s.lower() for s in serious_items)
    for sent in sentences:
        clean = sent.strip()
        if any(kw in clean.lower() for kw in serious_kw) and len(clean) > 10:
            short = clean[:100]
            if short.lower() not in existing:
                serious_items.append(short)
                existing.add(short.lower())
    serious_items[:] = serious_items[:6]


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
