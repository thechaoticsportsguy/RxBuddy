from __future__ import annotations

import logging
import os
import re
import sys

# Ensure the directory containing this file (backend/) is on sys.path so that
# sibling modules (answer_engine, label_updater) can be imported regardless of
# how uvicorn is invoked (Railway, local, Procfile, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
from typing import Optional

import anthropic
import requests as http_requests
from dotenv import load_dotenv
from spellchecker import SpellChecker

logger = logging.getLogger("rxbuddy")
logging.basicConfig(level=logging.INFO)
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import json
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, MetaData, Table, Text, create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, VARCHAR

# ── Answer Engine v2 ──────────────────────────────────────────────────────────
# Architecture: answer_engine.py  (intent classifier, citation models, retrieval guard)
# Cache/TTL:    label_updater.py  (24-h TTL cache, stale-refresh pipeline)
from answer_engine import (
    QuestionIntent,
    classify_intent,
    check_retrieval_guard,
    build_citations,
    build_refused_answer,
    extract_fda_metadata,
    Citation,
    RetrievalStatus,
    HIGH_RISK_DRUGS,
    HIGH_RISK_PAIRS,
    detect_emergency,
    check_high_risk_pair,
    build_emergency_answer,
    build_unknown_drug_answer,
)
import label_updater
from drug_catalog import find_drug, is_high_risk as catalog_is_high_risk, is_known_drug


# ---------- 1) Load secrets from .env ----------
# `.env` lives in your project root and is NOT committed to GitHub.
load_dotenv()


# ---------- 1b) Medical dictionary from RxNorm (cached on startup) ----------
# We fetch the top 1,000 drug names from RxNorm once at startup.
# These are added to the spell checker so drug names aren't flagged as misspelled.
RXNORM_DISPLAYNAMES_URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/displaynames.json"
RXNORM_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
MEDLINEPLUS_CONNECT_URL = "https://connect.medlineplus.gov/application"
RXIMAGE_API_URL = "https://rximage.nlm.nih.gov/api/rximage/1/rxbase"
_medical_terms: set[str] = set()
_spell_checker: SpellChecker | None = None


# ---------- FDA Label Fetching for Grounded Answers ----------
# Brand name → generic name mappings for FDA lookup
BRAND_TO_GENERIC = {
    "tylenol": "acetaminophen", "advil": "ibuprofen", "motrin": "ibuprofen",
    "aleve": "naproxen", "bayer": "aspirin", "excedrin": "acetaminophen",
    "benadryl": "diphenhydramine", "claritin": "loratadine", "zyrtec": "cetirizine",
    "allegra": "fexofenadine", "prilosec": "omeprazole", "nexium": "esomeprazole",
    "pepcid": "famotidine", "xanax": "alprazolam", "valium": "diazepam",
    "ambien": "zolpidem", "zoloft": "sertraline", "prozac": "fluoxetine",
    "lexapro": "escitalopram", "lipitor": "atorvastatin", "crestor": "rosuvastatin",
    "viagra": "sildenafil", "cialis": "tadalafil", "synthroid": "levothyroxine",
}


def _extract_drug_name(question: str) -> str | None:
    """
    Extract the primary drug name from a question.
    Converts brand names to generic names for FDA lookup.
    """
    q_lower = question.lower()
    
    # Check brand names first
    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower:
            return generic
    
    # Check common generic names
    generic_drugs = [
        "acetaminophen", "ibuprofen", "aspirin", "naproxen", "amoxicillin",
        "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
        "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
        "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
        "hydrocodone", "oxycodone", "tramadol", "warfarin", "ciprofloxacin",
    ]
    for drug in generic_drugs:
        if drug in q_lower:
            return drug

    return None


def _extract_drug_names(question: str) -> list[str]:
    """
    Extract ALL drug names mentioned in a question.

    Resolution order:
    1. BRAND_TO_GENERIC map (legacy fast path, kept for compat)
    2. Full drug_catalog (DrugRecord) — brand + canonical name matching
    3. services.drug_resolver — Levenshtein + RxNorm fallback for everything else
    """
    q_lower = question.lower()
    found: list[str] = []

    # Legacy brand map (fast, no import overhead)
    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower and generic not in found:
            found.append(generic)

    # Full catalog: check every canonical name and every brand alias
    try:
        from drug_catalog import _CATALOG, _ALIAS_MAP  # backend/drug_catalog.py
        # Canonical names
        for canonical_key in _CATALOG:
            if canonical_key in q_lower and canonical_key not in found:
                found.append(canonical_key)
        # Brand aliases → resolve to canonical
        for alias, canonical_key in _ALIAS_MAP.items():
            if alias in q_lower and canonical_key not in found:
                found.append(canonical_key)
    except Exception:
        pass  # catalog not available — fall through to resolver

    # Resolver-based extraction (catches misspellings + RxNorm unknowns)
    # Only runs if the catalog found nothing — avoids redundant work on
    # queries with well-known drug names.
    if not found:
        try:
            from services.drug_resolver import extract_generic_names
            resolved = extract_generic_names(question)
            for g in resolved:
                if g not in found:
                    found.append(g)
        except Exception:
            pass

    return found


def _classify_query_intent(question: str) -> str:
    """
    7-type intent classifier — delegates to answer_engine.classify_intent().

    Returns one of: interaction, dosing, side_effects, contraindications,
    pregnancy_lactation, food_alcohol, general.

    Replaces the legacy 5-type classifier. All callers receive a string value
    compatible with both the old TF-IDF reranking maps and the new retrieval guard.
    """
    drugs = _extract_drug_names(question)
    intent = classify_intent(question, drug_count=len(drugs))
    return intent.value


def _build_intent_query(question: str, intent: str, drugs: list[str]) -> str:
    """Append intent-specific keywords to improve TF-IDF retrieval accuracy."""
    boost = _INTENT_QUERY_BOOST.get(intent, "")
    return f"{question} {boost}".strip() if boost else question


def _rerank_by_intent(matches: list, drugs: list[str], intent: str) -> list:
    """
    Re-rank TF-IDF matches using intent + drug overlap signals.
    - +0.25 per drug from user query found in matched question
    - +0.30 if matched question's category matches user intent
    - -0.40 per drug missing from matched question (interaction queries only)
    """
    target_category = _INTENT_TO_CATEGORY.get(intent, "").lower()
    scored = []
    for m in matches:
        adjusted = m.score
        q_lower = (m.question or "").lower()
        cat = (m.category or "").lower()

        for drug in drugs:
            if drug in q_lower:
                adjusted += 0.25

        if target_category and cat == target_category:
            adjusted += 0.30

        if intent == "interaction" and len(drugs) >= 2:
            missing = sum(1 for drug in drugs if drug not in q_lower)
            adjusted -= missing * 0.40

        scored.append((adjusted, m))

    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored]


def _fetch_fda_label(drug_name: str) -> dict | None:
    """
    Fetch FDA label data (parsed sections only).
    Delegates to _fetch_fda_label_with_raw() so the label_updater cache is populated.
    Use _fetch_fda_label_with_raw() directly when you also need metadata (SetID, NDA, etc.).
    """
    fda_data, _ = _fetch_fda_label_with_raw(drug_name)
    return fda_data




def _openfda_search(field: str, value: str) -> "tuple[dict | None, dict | None]":
    """
    Single OpenFDA label search by field + value.
    Returns (parsed_fda_data, raw_label) or (None, None).
    Does NOT touch the cache — callers handle caching.
    """
    try:
        url = f"{OPENFDA_LABEL_URL}?search=openfda.{field}:\"{value}\"&limit=1"
        resp = http_requests.get(url, headers={"User-Agent": "RxBuddy/1.0"}, timeout=10)
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None, None

        raw_label = results[0]

        def get_section(k: str) -> str:
            val = raw_label.get(k, [])
            return val[0][:1200] if isinstance(val, list) and val else ""

        fda_data = {
            "drug_name":                  value,
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
        }
        has_data = any(v for k, v in fda_data.items() if k != "drug_name" and v)
        return (fda_data, raw_label) if has_data else (None, None)

    except Exception as exc:
        logger.debug("[FDA] _openfda_search %s=%s failed: %s", field, value, exc)
        return None, None


def _fetch_fda_label_with_raw(drug_name: str) -> "tuple[dict | None, dict | None]":
    """
    Fetch FDA label data for a drug name with multiple fallback strategies.

    Search order (stops at first hit):
      1. Cache (label_updater, 24-h TTL)
      2. openfda.generic_name:"name"   — standard generic name
      3. openfda.brand_name:"name"     — brand name (e.g. "Eliquis", "Lipitor")
      4. openfda.substance_name:"name" — INN substance name
      5. Catalog canonical name + brand names via drug_catalog

    Returns (parsed_fda_data, raw_openfda_result) or (None, None).
    """
    if not drug_name:
        return None, None

    key = drug_name.strip().lower()

    # 1. Cache hit
    cached = label_updater.get_cached_label(key)
    if cached is not None:
        logger.debug("[FDA] Cache HIT for '%s'", key)
        return cached.data, cached.raw_label

    logger.info("[FDA] Cache MISS — fetching label for '%s'", key)

    # Build the ordered list of (field, value) pairs to try
    search_attempts: list[tuple[str, str]] = [
        ("generic_name",   drug_name),
        ("brand_name",     drug_name),
        ("substance_name", drug_name),
    ]

    # Add catalog-derived names (brand ↔ generic resolution)
    try:
        rec = find_drug(drug_name)
        if rec:
            canonical = rec.canonical_name
            if canonical.lower() != key:
                search_attempts.append(("generic_name", canonical))
            for brand in rec.brand_names[:3]:   # cap at 3 brands to limit latency
                if brand.lower() != key:
                    search_attempts.append(("brand_name", brand))
    except Exception:
        pass

    fda_data, raw_label = None, None
    for field, value in search_attempts:
        fda_data, raw_label = _openfda_search(field, value)
        if fda_data:
            logger.info("[FDA] Resolved '%s' via openfda.%s=%s", drug_name, field, value)
            break

    if not fda_data:
        logger.info("[FDA] No label found for '%s' after all strategies", drug_name)
        return None, None

    # Store in cache under the original query key
    meta = extract_fda_metadata(raw_label)
    label_updater.put_label(
        drug_name=key,
        data=fda_data,
        raw_label=raw_label,
        label_revision_date=meta.get("label_revision_date"),
    )
    return fda_data, raw_label


def _fetch_all_labels(drug_names: list[str]) -> "dict[str, tuple[dict, dict]]":
    """
    Fetch FDA labels for every drug in drug_names.

    Returns a dict mapping each drug name to its (fda_data, raw_label).
    Drugs that could not be resolved are absent from the result.

    Used for multi-drug interaction queries so ALL labels are available
    for cross-referencing (e.g. checking whether drug A's interaction
    section mentions drug B by name).
    """
    result: dict[str, tuple[dict, dict]] = {}
    for name in drug_names:
        data, raw = _fetch_fda_label_with_raw(name)
        if data and raw:
            result[name] = (data, raw)
    return result


def _cross_reference_interactions(
    labels: "dict[str, tuple[dict, dict]]",
) -> "list[str]":
    """
    Scan every drug's drug_interactions section for mentions of other drugs.

    Returns a list of plain-English signals like:
      "apixaban label mentions 'ibuprofen': ... relevant text snippet ..."
    These are injected into the FDA context so Claude has explicit evidence.
    """
    signals: list[str] = []
    drug_names = list(labels.keys())
    for source_drug, (fda_data, _) in labels.items():
        interactions_text = fda_data.get("drug_interactions", "").lower()
        if not interactions_text:
            continue
        for target_drug in drug_names:
            if target_drug == source_drug:
                continue
            # Search for target drug and its known aliases in source drug's interaction section
            search_terms = [target_drug]
            try:
                rec = find_drug(target_drug)
                if rec:
                    search_terms.append(rec.canonical_name)
                    search_terms.extend(rec.brand_names[:2])
            except Exception:
                pass

            for term in search_terms:
                idx = interactions_text.find(term.lower())
                if idx != -1:
                    # Extract a snippet around the mention
                    start = max(0, idx - 60)
                    end   = min(len(interactions_text), idx + 200)
                    snippet = fda_data["drug_interactions"][start:end].strip()
                    signals.append(
                        f"[FDA INTERACTION SIGNAL] {source_drug.upper()} label "
                        f"mentions '{term}': \"...{snippet}...\""
                    )
                    break  # one signal per pair is enough

    return signals


def _build_multi_drug_context(
    labels: "dict[str, tuple[dict, dict]]",
    question: str,
    medlineplus_data: "dict | None" = None,
) -> str:
    """
    Build a grounded FDA context string for multi-drug queries.

    Includes:
      - Boxed warnings for each drug
      - Drug interactions section for each drug
      - Cross-reference signals (does drug A's label mention drug B?)
      - Warnings / contraindications / pregnancy sections as relevant
    """
    if not labels and not medlineplus_data:
        return ""

    q_lower = question.lower()
    parts: list[str] = []

    for drug_name, (fda_data, _) in labels.items():
        drug_parts: list[str] = [f"━━━ FDA LABEL: {drug_name.upper()} ━━━"]

        # Boxed warning always included (highest severity)
        if fda_data.get("boxed_warning"):
            drug_parts.append(f"BOXED WARNING: {fda_data['boxed_warning'][:600]}")

        # Drug interactions always included for multi-drug queries
        if fda_data.get("drug_interactions"):
            drug_parts.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:700]}")

        # Warnings
        if fda_data.get("warnings"):
            drug_parts.append(f"WARNINGS: {fda_data['warnings'][:500]}")

        # Contraindications
        if fda_data.get("contraindications"):
            drug_parts.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:400]}")

        # Pregnancy only if relevant
        if any(w in q_lower for w in ("pregnant", "pregnancy", "breastfeed", "nursing", "lactation")):
            for section in ("pregnancy", "lactation", "use_in_specific_populations"):
                if fda_data.get(section):
                    drug_parts.append(f"{section.upper()}: {fda_data[section][:400]}")

        if len(drug_parts) > 1:   # more than just the header
            parts.append("\n".join(drug_parts))

    # Cross-reference: does drug A's interaction list mention drug B?
    signals = _cross_reference_interactions(labels)
    if signals:
        parts.append("━━━ CROSS-REFERENCE SIGNALS (extracted from FDA labels) ━━━")
        parts.extend(signals)

    # MedlinePlus for patient-friendly context only
    if medlineplus_data:
        ml_parts = ["━━━ MEDLINEPLUS (plain-English supplement only) ━━━"]
        if medlineplus_data.get("summary"):
            ml_parts.append(f"SUMMARY: {medlineplus_data['summary'][:400]}")
        if medlineplus_data.get("side_effects"):
            ml_parts.append(f"SIDE EFFECTS: {medlineplus_data['side_effects'][:300]}")
        parts.append("\n".join(ml_parts))

    return "\n\n".join(parts)


def _build_fda_context(fda_data: dict | None, question: str, medlineplus_data: dict | None = None) -> str:
    """
    Build a grounded context string for Claude.
    FDA clinical data is the source of truth for interactions, contraindications,
    warnings, and severity. MedlinePlus is included only for patient-friendly
    explanations and side-effect descriptions.
    """
    if not fda_data and not medlineplus_data:
        return ""

    q_lower = question.lower()

    fda_sections: list[str] = []
    medline_sections: list[str] = []
    drug_name = (fda_data or {}).get("drug_name", "Unknown")

    if fda_data:
        fda_sections.append("FDA CLINICAL DATA")
        fda_sections.append(f"Drug: {drug_name}")

        if fda_data.get("indications_and_usage"):
            fda_sections.append(f"INDICATIONS: {fda_data['indications_and_usage'][:400]}")

        if any(w in q_lower for w in ["dose", "dosage", "how much", "how many", "take"]):
            if fda_data.get("dosage_and_administration"):
                fda_sections.append(f"DOSAGE AND ADMINISTRATION: {fda_data['dosage_and_administration'][:500]}")

        if any(w in q_lower for w in ["warning", "danger", "risk", "safe", "unsafe"]):
            if fda_data.get("warnings"):
                fda_sections.append(f"WARNINGS: {fda_data['warnings'][:500]}")

        if any(w in q_lower for w in ["interact", "interaction", "with", "mix", "combine", "together"]):
            if fda_data.get("drug_interactions"):
                fda_sections.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:500]}")

        if any(w in q_lower for w in ["shouldn't", "should not", "can't", "cannot", "avoid", "contraindication"]):
            if fda_data.get("contraindications"):
                fda_sections.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:500]}")

        if any(w in q_lower for w in ["pregnant", "pregnancy", "breastfeed", "nursing"]):
            if fda_data.get("pregnancy"):
                fda_sections.append(f"PREGNANCY: {fda_data['pregnancy'][:500]}")

        if any(w in q_lower for w in ["side effect", "reaction", "adverse"]):
            if fda_data.get("adverse_reactions"):
                fda_sections.append(f"ADVERSE REACTIONS: {fda_data['adverse_reactions'][:500]}")

        if len(fda_sections) <= 2:
            if fda_data.get("warnings"):
                fda_sections.append(f"WARNINGS: {fda_data['warnings'][:400]}")
            if fda_data.get("contraindications"):
                fda_sections.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:400]}")
            if fda_data.get("drug_interactions"):
                fda_sections.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:400]}")
            if fda_data.get("dosage_and_administration"):
                fda_sections.append(f"DOSAGE AND ADMINISTRATION: {fda_data['dosage_and_administration'][:300]}")

    if medlineplus_data:
        medline_sections.append("MEDLINEPLUS PATIENT SUMMARY")
        medline_sections.append("Use this section only for plain-English explanation and side-effect descriptions.")
        if medlineplus_data.get("summary"):
            medline_sections.append(f"SUMMARY: {medlineplus_data['summary']}")
        if medlineplus_data.get("usage"):
            medline_sections.append(f"PLAIN-ENGLISH USE: {medlineplus_data['usage']}")
        if medlineplus_data.get("side_effects"):
            medline_sections.append(f"PATIENT SIDE EFFECTS: {medlineplus_data['side_effects']}")

    sections: list[str] = []
    if fda_sections:
        sections.append("\n".join(fda_sections))
    if medline_sections:
        sections.append("\n".join(medline_sections))

    return "\n\n".join(sections)


def _fetch_rxnorm_drug_names(limit: int = 1000) -> set[str]:
    """
    Fetch drug names from RxNorm Prescribe API (displaynames endpoint).
    Returns a set of lowercase drug names for spell checking.
    """
    try:
        logger.info("[RxNorm] Fetching drug names from %s...", RXNORM_DISPLAYNAMES_URL)
        resp = http_requests.get(
            RXNORM_DISPLAYNAMES_URL,
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        terms = data.get("displayTermsList", {}).get("term", [])
        if not isinstance(terms, list):
            terms = [terms] if terms else []
        # Take first N terms, lowercase, filter short/non-alpha
        drugs = set()
        for t in terms[:limit]:
            name = str(t).strip().lower()
            # Only add reasonable drug names (3+ chars, mostly letters)
            if len(name) >= 3 and any(c.isalpha() for c in name):
                drugs.add(name)
                # Also add individual words for multi-word drug names
                for word in name.split():
                    if len(word) >= 3 and word.isalpha():
                        drugs.add(word)
        logger.info("[RxNorm] Loaded %d medical terms", len(drugs))
        return drugs
    except Exception as e:
        logger.warning("[RxNorm] Failed to fetch drug names: %s", e)
        return set()


def _fetch_rxcui(drug_name: str) -> str | None:
    """
    Look up RxCUI for a drug name using the RxNorm API.
    Returns the RxCUI string or None if not found.
    """
    if not drug_name:
        return None
    try:
        resp = http_requests.get(
            RXNORM_RXCUI_URL,
            params={"name": drug_name, "allSourcesFlag": "0"},
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        rxcui = data.get("idGroup", {}).get("rxnormId", [None])[0]
        if rxcui:
            logger.info("[RxNorm] RxCUI for '%s': %s", drug_name, rxcui)
        return rxcui
    except Exception as e:
        logger.warning("[RxNorm] Failed to fetch RxCUI for '%s': %s", drug_name, e)
        return None


def _fetch_medlineplus(drug_name: str) -> dict | None:
    """
    Fetch patient-friendly drug information from MedlinePlus Connect.
    Uses the drug's RxCUI as the lookup key.
    Returns dict with keys: summary, usage, side_effects — or None if unavailable.
    """
    if not drug_name:
        return None
    try:
        rxcui = _fetch_rxcui(drug_name)
        if not rxcui:
            return None

        resp = http_requests.get(
            MEDLINEPLUS_CONNECT_URL,
            params={
                "mainSearchCriteria.v.cs": "2.16.840.1.113883.6.88",
                "mainSearchCriteria.v.c": rxcui,
                "knowledgeResponseType": "application/json",
            },
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # MedlinePlus Connect returns a feed with entry list
        feed = data.get("feed", {})
        entries = feed.get("entry", [])
        if not entries:
            return None

        entry = entries[0]
        summary = entry.get("summary", {}).get("_value", "")
        title = entry.get("title", {}).get("_value", "")

        # Extract content sections — MedlinePlus wraps HTML; strip tags for plain text
        content = entry.get("content", {}).get("_value", "")
        clean = re.sub(r"<[^>]+>", " ", content)
        clean = re.sub(r"\s+", " ", clean).strip()

        # Keep it concise for Claude context (max 600 chars)
        result = {
            "summary": (summary or title)[:300],
            "usage": clean[:300] if clean else "",
            "side_effects": "",  # Extracted from content if present
        }

        # Try to pull a side-effects sentence from content
        lower_clean = clean.lower()
        se_idx = lower_clean.find("side effect")
        if se_idx != -1:
            result["side_effects"] = clean[se_idx: se_idx + 250]

        has_data = any(result[k] for k in ("summary", "usage", "side_effects"))
        if not has_data:
            return None

        logger.info("[MedlinePlus] Fetched info for '%s'", drug_name)
        return result

    except Exception as e:
        logger.warning("[MedlinePlus] Failed for '%s': %s", drug_name, e)
        return None


def _fetch_pill_image(drug_name: str) -> str | None:
    """
    Fetch a real pill photo URL from NIH RxImageAccess API.
    Returns the thumbnail image URL string or None if not found.
    Falls back gracefully — never raises.
    """
    if not drug_name:
        return None
    try:
        resp = http_requests.get(
            RXIMAGE_API_URL,
            params={"name": drug_name, "resolution": "thumbnail"},
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("nlmRxImages", [])
        if images:
            image_url = images[0].get("imageUrl")
            if image_url:
                logger.info("[RxImage] Found pill image for '%s': %s", drug_name, image_url)
                return image_url
        return None
    except Exception as e:
        logger.warning("[RxImage] Failed for '%s': %s", drug_name, e)
        return None


def _init_spell_checker() -> None:
    """Initialize the spell checker with medical terms. Called once at startup."""
    global _medical_terms, _spell_checker
    _medical_terms = _fetch_rxnorm_drug_names(limit=1000)
    _spell_checker = SpellChecker()
    # Add medical terms to the spell checker's known words
    if _medical_terms:
        _spell_checker.word_frequency.load_words(_medical_terms)
    # Also add common drug-related words
    extra_medical = {
        "tylenol", "ibuprofen", "acetaminophen", "naproxen", "aspirin",
        "amoxicillin", "metformin", "lisinopril", "omeprazole", "gabapentin",
        "medication", "prescription", "dosage", "antibiotic", "painkiller",
        "allergic", "interaction", "contraindication", "pharmacy", "pharmacist",
    }
    _spell_checker.word_frequency.load_words(extra_medical)
    logger.info("[SpellChecker] Initialized with %d medical terms + %d extra", len(_medical_terms), len(extra_medical))


def spell_check_query(query: str) -> str | None:
    """
    Run spell check on a query. Returns corrected query if changes were made, else None.
    Skips words that are in the medical dictionary.
    """
    if _spell_checker is None:
        return None
    words = query.lower().split()
    if not words:
        return None
    corrected = []
    changed = False
    for word in words:
        # Skip punctuation-only or very short words
        clean = re.sub(r"[^\w]", "", word)
        if len(clean) < 3:
            corrected.append(word)
            continue
        # Skip if it's a known medical term
        if clean in _medical_terms:
            corrected.append(word)
            continue
        # Skip if it's in the spell checker's dictionary
        if clean in _spell_checker:
            corrected.append(word)
            continue
        # Get correction suggestion
        correction = _spell_checker.correction(clean)
        if correction and correction != clean:
            # Preserve original punctuation
            new_word = word.replace(clean, correction)
            corrected.append(new_word)
            changed = True
        else:
            corrected.append(word)
    if changed:
        return " ".join(corrected)
    return None


# ---------- Confidence threshold and self-learning ----------
CONFIDENCE_THRESHOLD = 0.35  # If best match score is below this, use Claude for live answer

# ── Deterministic verdict table ───────────────────────────────────────────────
# Pre-resolved answers for the highest-stakes drug combinations.
# Format: frozenset({drug_a, drug_b}) → (VERDICT, DIRECT sentence)
# These SHORT-CIRCUIT Claude — no API call, no hallucination risk.
# Use canonical lowercase names (after drug_catalog normalisation).
_DETERMINISTIC_VERDICTS: dict[frozenset, tuple[str, str]] = {
    # Anticoagulant + NSAID → major bleeding
    frozenset({"warfarin",    "aspirin"}):    ("AVOID",    "Do not combine warfarin and aspirin without medical supervision — the risk of serious bleeding is significantly increased."),
    frozenset({"warfarin",    "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with warfarin — NSAIDs substantially raise your bleeding risk and can raise warfarin levels."),
    frozenset({"warfarin",    "naproxen"}):   ("AVOID",    "Do not take naproxen with warfarin — this combination significantly increases the risk of serious or fatal bleeding."),
    frozenset({"warfarin",    "diclofenac"}): ("AVOID",    "Do not take diclofenac with warfarin — the combination raises bleeding risk and can make anticoagulation unstable."),
    frozenset({"apixaban",    "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with apixaban (Eliquis) — NSAIDs increase bleeding risk with all blood thinners."),
    frozenset({"apixaban",    "aspirin"}):    ("CAUTION",  "Use extreme caution combining apixaban and aspirin — dual use significantly raises bleeding risk; only do so if a doctor has explicitly prescribed both."),
    frozenset({"rivaroxaban", "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with rivaroxaban (Xarelto) — NSAIDs increase bleeding risk when combined with blood thinners."),
    frozenset({"rivaroxaban", "aspirin"}):    ("CAUTION",  "Use extreme caution — combining rivaroxaban with aspirin increases bleeding risk and should only be done under medical supervision."),
    frozenset({"dabigatran",  "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with dabigatran (Pradaxa) — this combination significantly increases the risk of gastrointestinal bleeding."),
    # Nitrate + PDE-5 → severe hypotension / fatal
    frozenset({"sildenafil",  "nitroglycerin"}):           ("AVOID", "Never combine sildenafil (Viagra) with nitroglycerin — the combination can cause a life-threatening drop in blood pressure."),
    frozenset({"tadalafil",   "nitroglycerin"}):           ("AVOID", "Never combine tadalafil (Cialis) with nitroglycerin — this can cause a dangerous and potentially fatal drop in blood pressure."),
    frozenset({"sildenafil",  "isosorbide mononitrate"}):  ("AVOID", "Never combine sildenafil with isosorbide mononitrate — the combination causes severe, potentially fatal hypotension."),
    # Serotonin syndrome
    frozenset({"sertraline",  "tramadol"}):   ("AVOID",    "Do not take tramadol with sertraline — this combination can cause serotonin syndrome, a potentially life-threatening condition."),
    frozenset({"fluoxetine",  "tramadol"}):   ("AVOID",    "Do not take tramadol with fluoxetine — the combination raises the risk of serotonin syndrome, which can be life-threatening."),
    frozenset({"linezolid",   "sertraline"}): ("AVOID",    "Do not take linezolid with sertraline — linezolid has MAOI properties that can cause fatal serotonin syndrome."),
    # Narrow-TI drug toxicity
    frozenset({"lithium",     "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with lithium — NSAIDs reduce lithium excretion and can quickly push levels into the toxic range."),
    frozenset({"lithium",     "naproxen"}):   ("AVOID",    "Do not take naproxen with lithium — NSAIDs impair lithium clearance and can cause toxicity."),
    frozenset({"digoxin",     "amiodarone"}): ("AVOID",    "Do not combine digoxin with amiodarone without close monitoring — amiodarone doubles digoxin levels, risking toxicity."),
    frozenset({"digoxin",     "verapamil"}):  ("AVOID",    "Do not combine digoxin with verapamil without medical supervision — verapamil raises digoxin levels and increases toxicity risk."),
    frozenset({"methotrexate","ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with methotrexate — NSAIDs reduce methotrexate clearance and can cause serious or fatal toxicity."),
    frozenset({"methotrexate","naproxen"}):   ("AVOID",    "Do not take naproxen with methotrexate — this combination can cause life-threatening methotrexate toxicity."),
    # Retinoid + tetracycline → pseudotumor cerebri
    frozenset({"isotretinoin","doxycycline"}):("AVOID",    "Do not combine isotretinoin with doxycycline — both drugs raise intracranial pressure and the combination risks pseudotumor cerebri."),
    frozenset({"isotretinoin","minocycline"}):("AVOID",    "Do not combine isotretinoin with minocycline — this combination significantly increases the risk of dangerously raised intracranial pressure."),
    # Kidney / lactic acidosis risk
    frozenset({"metformin",   "ibuprofen"}):  ("CAUTION",  "Use caution — ibuprofen can impair kidney function, which reduces metformin clearance and raises lactic acidosis risk; prefer acetaminophen for pain."),
    frozenset({"lisinopril",  "ibuprofen"}):  ("CAUTION",  "Use caution — ibuprofen reduces the blood-pressure-lowering effect of lisinopril and together with an ACE inhibitor can cause acute kidney injury."),
    frozenset({"lisinopril",  "potassium chloride"}): ("CAUTION", "Use caution — lisinopril already raises potassium levels; adding potassium supplements can cause dangerously high potassium (hyperkalemia)."),
}

# Tylenol/acetaminophen + alcohol is an intent-specific case (food_alcohol only)
_ACETAMINOPHEN_ALCOHOL_DIRECT = (
    "CAUTION",
    "Use caution — occasional light drinking is unlikely to cause harm, but regular or heavy alcohol use with acetaminophen significantly increases the risk of liver damage.",
)

# ── Vague-phrase ban list ─────────────────────────────────────────────────────
# Any DIRECT line containing one of these phrases must be flagged and rewritten.
_VAGUE_BANNED: frozenset[str] = frozenset({
    "it depends",
    "generally okay",
    "generally safe",
    "may be safe",
    "could be safe",
    "might be safe",
    "usually safe",
    "typically safe",
    "often safe",
    "in most cases",
    "for most people",
    "varies",
    "you should consult",   # too vague without a reason
    "check with your",      # acceptable only in DO/DOCTOR, not DIRECT
    "always check",
    "be careful",
})


def _check_vague(answer_text: str) -> bool:
    """
    Return True if the ANSWER (or legacy DIRECT) line contains banned vague phrases.
    Only checks the primary answer line — other sections may use softer language.
    """
    for line in answer_text.splitlines():
        upper = line.strip().upper()
        if upper.startswith("ANSWER:") or upper.startswith("DIRECT:"):
            line_lower = line.lower()
            return any(phrase in line_lower for phrase in _VAGUE_BANNED)
    return False


def _lookup_deterministic(drug_names: list[str], intent: str) -> tuple[str, str] | None:
    """
    Check _DETERMINISTIC_VERDICTS for a pre-resolved answer.

    Returns (VERDICT, DIRECT) if a match is found, else None.
    Also handles the acetaminophen+alcohol special case.
    """
    lower = {d.lower() for d in drug_names}
    pair = frozenset(lower)

    # Special case: acetaminophen (or tylenol) + alcohol, food_alcohol intent
    alcohol_terms = {"alcohol", "drinking", "drink", "beer", "wine"}
    aceta_terms   = {"acetaminophen", "tylenol", "paracetamol"}
    if intent in ("food_alcohol", "side_effects", "interaction") and lower & alcohol_terms and lower & aceta_terms:
        return _ACETAMINOPHEN_ALCOHOL_DIRECT

    if len(lower) >= 2:
        return _DETERMINISTIC_VERDICTS.get(pair)

    return None

# Key concept words that MUST appear in the matched DB question when they appear in the user
# query. If the best TF-IDF match is missing any of these → force Claude to generate a fresh
# answer instead of returning a potentially-off-topic DB row.
_REQUIRED_CONCEPT_WORDS: frozenset[str] = frozenset({
    "alcohol", "alcoholic", "drinking", "drink", "beer", "wine", "liquor",
    "pregnant", "pregnancy", "breastfeeding", "nursing", "lactation",
    "liver", "kidney", "renal", "hepatic",
    "overdose", "overdosed",
    "child", "children", "pediatric", "infant", "baby",
    "elderly",
})

# Intent classification signals — keyword → intent category
_INTENT_SIGNALS: dict[str, list[str]] = {
    "interaction": ["interact", "combine", "together", "mix", "take with", "and ", "both"],
    "dosage": ["dose", "dosage", "how much", "how many", "mg", "milligram", "strength", "maximum dose"],
    "side_effects": ["side effect", "adverse", "cause", "feel", "happen if", "reaction", "symptom"],
    "pregnancy": ["pregnant", "pregnancy", "breastfeed", "nursing", "baby", "fetus"],
    "storage": ["store", "storage", "expire", "expiration", "refrigerat", "shelf life"],
    "alcohol": ["alcohol", "drink", "beer", "wine", "liquor"],
    "overdose": ["overdose", "too much", "too many", "maximum", "excess"],
    "safety": ["safe", "okay to", "is it okay", "can i take", "should i", "dangerous"],
}

# Extra keywords appended to search query per intent — improves TF-IDF retrieval accuracy
_INTENT_QUERY_BOOST: dict[str, str] = {
    "interaction": "interaction drug combination effect",
    "what_is": "what is drug used for treats indication mechanism",
    "side_effects": "side effects adverse reactions symptoms",
    "dosing": "dosage dose mg adults",
    "safety": "safe safety risk warning precaution",
    "contraindications": "contraindications warnings precautions",
    "pregnancy_lactation": "pregnancy safe pregnant breastfeeding",
    "food_alcohol": "alcohol food drink interaction",
    "general": "drug information overview",
}

# Intent → DB category name (must match PHARMACY_CATEGORIES values exactly)
_INTENT_TO_CATEGORY: dict[str, str] = {
    "interaction": "Drug Interactions",
    "what_is": "General",
    "side_effects": "Side Effects",
    "dosing": "Dosage",
    "safety": "Warnings",
    "contraindications": "Warnings",
    "pregnancy_lactation": "Pregnancy",
    "food_alcohol": "Alcohol",
    "general": "General",
}

# 16 pharmacy categories for self-learning classification
PHARMACY_CATEGORIES = [
    "Drug Interactions",
    "Dosage",
    "Side Effects",
    "Warnings",
    "Contraindications",
    "Pregnancy",
    "Storage",
    "Children",
    "Special Populations",
    "Overdose",
    "Adverse Reactions",
    "General",
    "Patient Counseling",
    "Patient Information",
    "Alcohol",
    "Food Interactions",
]


def _is_valid_pharmacy_question(question: str) -> bool:
    """
    Ask Claude if this is a valid pharmacy question worth saving.
    Returns True if YES, False otherwise.
    """
    api_key = _anthropic_api_key()
    if not api_key:
        return False

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)
        prompt = (
            f"Is this a valid pharmacy/medication question worth saving to a medical FAQ database? "
            f"Answer only YES or NO.\n\nQuestion: {question}"
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.content[0].text or "").strip().upper()
        return text.startswith("YES")
    except Exception as e:
        logger.warning("[Claude] Failed to validate question: %s", e)
        return False


def _get_best_category(question: str) -> str:
    """
    Ask Claude to assign the best category from our 16 categories.
    Returns category name or "General" as fallback.
    """
    api_key = _anthropic_api_key()
    if not api_key:
        return "General"

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)
        categories_str = ", ".join(PHARMACY_CATEGORIES)
        prompt = (
            f"Classify this pharmacy question into exactly one category.\n"
            f"Categories: {categories_str}\n\n"
            f"Question: {question}\n\n"
            f"Reply with only the category name, nothing else."
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.content[0].text or "").strip()
        # Find closest matching category
        for cat in PHARMACY_CATEGORIES:
            if cat.lower() in text.lower():
                return cat
        return "General"
    except Exception as e:
        logger.warning("[Claude] Failed to categorize question: %s", e)
        return "General"


def _save_question_to_db(question: str, answer: str, category: str) -> int | None:
    """
    Save a new question + answer to the questions table.
    Returns the new question ID or None on failure.
    
    After saving, rebuilds the TF-IDF index so the new question is searchable.
    """
    logger.info("[Self-Learning] Saving new question to database: %.80s", question)
    
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            r = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM questions"))
            next_id = int(r.scalar() or 0) + 1

        # Extract tags from question (simple heuristic: drug names)
        q_lower = question.lower()
        tags = [category.lower()]
        # Add common drug names if found
        common_drugs = [
            "ibuprofen", "tylenol", "acetaminophen", "aspirin", "naproxen", 
            "amoxicillin", "metformin", "lisinopril", "omeprazole", "gabapentin",
            "sertraline", "xanax", "alprazolam", "prednisone", "advil", "aleve",
        ]
        for drug in common_drugs:
            if drug in q_lower:
                tags.append(drug)

        with engine.begin() as conn:
            conn.execute(
                questions_table.insert().values(
                    id=next_id,
                    question=question,
                    category=category,
                    tags=tags,
                    answer=answer,
                    created_at=_utc_now(),
                )
            )
        
        logger.info("[Self-Learning] Successfully saved question #%d with answer length: %d chars", 
                    next_id, len(answer))
        
        # Rebuild TF-IDF index so the new question is immediately searchable
        try:
            from ml.tfidf_search import rebuild_index
            rebuild_index()
            logger.info("[Self-Learning] TF-IDF index rebuilt to include new question")
        except Exception as rebuild_err:
            logger.warning("[Self-Learning] Could not rebuild TF-IDF index: %s", rebuild_err)
        
        return next_id
        
    except Exception as e:
        logger.error("[Self-Learning] Failed to save question: %s", e, exc_info=True)
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _database_url() -> str:
    """
    Reads DATABASE_URL from the environment.

    We also force the 'psycopg' driver (postgresql+psycopg://...) because this project
    installs `psycopg[binary]` (not psycopg2).
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Create a .env file in the project root with:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:6767/rxbuddy"
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _anthropic_api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return key or None


_has_anthropic_key = bool(_anthropic_api_key())
logger.info("ANTHROPIC_API_KEY configured: %s", _has_anthropic_key)

# ── In-memory answer cache ────────────────────────────────────────────────────
# Keyed by normalised query string; value is (answer_text, citations, intent, rs, timestamp).
# 1-hour TTL; evict oldest 100 entries when the cap is reached.
_ANSWER_CACHE: dict[str, tuple] = {}
_CACHE_TTL = 3600        # seconds
_CACHE_MAX = 500         # max entries before eviction


def _cache_get(key: str) -> "tuple | None":
    entry = _ANSWER_CACHE.get(key)
    if not entry:
        return None
    *payload, ts = entry
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL:
        _ANSWER_CACHE.pop(key, None)
        return None
    return tuple(payload)


def _cache_set(key: str, answer_text: str, citations: list, intent_str: str, rs: str) -> None:
    if len(_ANSWER_CACHE) >= _CACHE_MAX:
        # Evict the 100 oldest entries by insertion-order (dicts are ordered in Py 3.7+)
        to_evict = list(_ANSWER_CACHE.keys())[:100]
        for k in to_evict:
            _ANSWER_CACHE.pop(k, None)
    _ANSWER_CACHE[key] = (answer_text, citations, intent_str, rs, datetime.now(timezone.utc).timestamp())


# ---------- Slang normalization (zero API calls, runs before every search) ----------
SPELLING_FIXES = {
    "tynenol": "tylenol",
    "ibrofen": "ibuprofen",
    "ibuprofin": "ibuprofen",
    "amoxicilin": "amoxicillin",
    "motrin": "ibuprofen",
    "advil": "ibuprofen",
    "aleve": "naproxen",
    "nyquil": "dextromethorphan",
}

SLANG_MAP = {
    "drunk": "alcohol",
    "wasted": "alcohol",
    "booze": "alcohol",
    "drinking": "alcohol",
    "cooked": "dangerous reaction",
    "meds": "medication",
    "pills": "medication",
    "xanny": "xanax",
    "addy": "adderall",
    "molly": "MDMA",
    "high": "intoxicated",
    "stoned": "cannabis",
    "head is killing me": "headache",
    "stomach is killing me": "stomach pain",
    "throwing up": "vomiting",
    "threw up": "vomiting",
    "feel sick": "nausea",
    "heart racing": "palpitations",
    "can't sleep": "insomnia",
    "knocked out": "sedated",
}

FILLER_WORDS = frozenset({"like", "um", "basically", "literally", "yo", "bro", "hey"})


def normalize_query(query: str) -> tuple[str, str]:
    """
    Preprocess user query for better search: fix misspellings, replace slang,
    remove filler words. Zero API calls.

    Returns (original_query, cleaned_query).
    Show original to user; search with cleaned.
    """
    original = (query or "").strip()
    if not original:
        return original, original

    q = original.lower()

    # 1) Replace slang phrases first (longer matches before shorter)
    for slang, medical in sorted(SLANG_MAP.items(), key=lambda x: -len(x[0])):
        # Word-boundary aware: replace whole words/phrases
        pattern = r"\b" + re.escape(slang) + r"\b"
        q = re.sub(pattern, medical, q, flags=re.IGNORECASE)

    # 2) Fix drug misspellings
    for misspelling, correct in SPELLING_FIXES.items():
        pattern = r"\b" + re.escape(misspelling) + r"\b"
        q = re.sub(pattern, correct, q, flags=re.IGNORECASE)

    # 3) Remove filler words
    words = q.split()
    words = [w for w in words if w.lower() not in FILLER_WORDS]

    # 4) Normalize whitespace
    cleaned = " ".join(words).strip()

    return original, cleaned if cleaned else original


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return (" ".join(words[:max_words])).strip()


def _validate_ai_answer(question: str, answer: str) -> str:
    api_key = _anthropic_api_key()
    if not api_key:
        return answer
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)
        validation_prompt = f"""Check this medication answer for accuracy and verdict consistency:

ORIGINAL QUESTION: {question}
ANSWER TO CHECK: {answer}

Verify:
1. Does the answer ONLY mention drugs from the original question?
2. Does it directly answer the question type (dosing/interaction/pregnancy/side effects)?
3. Is there any unrelated medication mentioned?
4. Does the VERDICT match the explanation?
5. If the explanation mentions a moderate interaction, monitoring, kidney strain, lactic acidosis risk, or "use with caution", the VERDICT must be CAUTION and never SAFE.
6. If the explanation mentions a serious interaction, contraindication, major bleeding risk, or "do not take together", the VERDICT must be AVOID.

If any issue found → rewrite the answer correctly following the same output structure.
If answer is correct → return it unchanged.
If any issue is found, rewrite using EXACTLY this structure:
VERDICT: ...
ANSWER: [one decisive sentence]
WARNING: [safety warning — omit line if SAFE with no caveats]
DETAILS: fact 1 | fact 2 | fact 3
ACTION: action 1 | action 2 | action 3
ARTICLE: [1-3 sentence mechanism/context explanation]
CONFIDENCE: ...
SOURCES: ...
Return ONLY the final answer, no commentary."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": validation_prompt}],
        )
        return (response.content[0].text or "").strip() or answer
    except Exception:
        return answer


def _generate_ai_answer(question: str) -> "tuple[str, list[dict], str, str]":
    """
    Generate a grounded answer using Claude Sonnet.

    Returns
    -------
    (answer_text, citations, intent_str, retrieval_status_str)
      answer_text       – structured text (VERDICT/DIRECT/WHY/DO/AVOID/DOCTOR/CONFIDENCE/SOURCES)
      citations         – list of serialised Citation dicts from answer_engine.build_citations()
      intent_str        – QuestionIntent value (e.g. "interaction", "dosing")
      retrieval_status_str – RetrievalStatus value (e.g. "LABEL_FOUND", "REFUSED_NO_SOURCE")

    Guardrails
    ----------
    1. Retrieval guard  – refuses dosing/contraindications/pregnancy without an FDA label
    2. FDA grounding    – label data fetched via _fetch_fda_label_with_raw() (cached 24 h)
    3. Citation objects – built from raw OpenFDA response (SetID, NDA, revision date)
    4. Confidence score – HIGH/MEDIUM/LOW returned with every answer
    5. Validator pass   – Claude Haiku checks verdict consistency post-generation
    """
    # ── Cache check — skip Claude entirely if we have a fresh answer ─────────
    _cache_key = question.strip().lower()
    _cached = _cache_get(_cache_key)
    if _cached:
        logger.info("[Cache] HIT for: %.80s", question)
        return _cached  # (answer_text, citations, intent_str, retrieval_status_str)

    api_key = _anthropic_api_key()
    if not api_key:
        logger.error("[Claude] ANTHROPIC_API_KEY is NOT set in environment!")
        raise RuntimeError("ANTHROPIC_API_KEY is missing.")

    logger.info("[Claude] API key found (first 8 chars): %s...", api_key[:8] if len(api_key) > 8 else "***")

    # ── Emergency detection — must happen before any other logic ──────────────
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    if detect_emergency(question):
        logger.warning("[Guardrail] Emergency keywords detected in query: %.80s", question)
        emergency = build_emergency_answer(fetched_at)
        emergency_text = (
            f"VERDICT: EMERGENCY\n"
            f"ANSWER: {emergency.short_answer}\n"
            f"WARNING: This is a medical emergency — do not wait.\n"
            f"DETAILS: Immediate action is required | Do not induce vomiting unless directed by poison control | Stay on the line with emergency services\n"
            f"ACTION: {' | '.join(emergency.emergency_escalation)}\n"
            f"ARTICLE: If you or someone else is experiencing a medical emergency related to medication, call 911 or Poison Control (1-800-222-1222) immediately. Do not wait for symptoms to worsen.\n"
            f"CONFIDENCE: HIGH\n"
            f"SOURCES: Emergency Services"
        )
        return emergency_text, [], QuestionIntent.GENERAL.value, RetrievalStatus.REFUSED_NO_SOURCE.value

    # Ground answers in FDA + MedlinePlus data — extract all drugs, fetch label for primary
    drug_names = _extract_drug_names(question)

    # Normalise drug names via drug catalog (brand → generic, spell-correct)
    normalised: list[str] = []
    for raw_name in drug_names:
        rec = find_drug(raw_name)
        normalised.append(rec.canonical_name if rec else raw_name)
    drug_names = normalised

    drug_name = drug_names[0] if drug_names else _extract_drug_name(question)

    # Unknown drug fallback: if we found a name but neither catalog nor OpenFDA knows it
    if drug_name and not is_known_drug(drug_name):
        fda_check, _ = _fetch_fda_label_with_raw(drug_name)
        if not fda_check:
            logger.info("[Guardrail] Unknown drug '%s' — returning unknown-drug answer", drug_name)
            unknown = build_unknown_drug_answer(drug_name, fetched_at)
            unknown_text = (
                f"VERDICT: CONSULT_PHARMACIST\n"
                f"ANSWER: {unknown.short_answer}\n"
                f"WARNING: This drug was not found in our database — verify its name and consult a pharmacist.\n"
                f"DETAILS: Drug name not found in FDA database | Safety information unavailable without verified label | A pharmacist can look up the full drug record\n"
                f"ACTION: {' | '.join(unknown.what_to_do)}\n"
                f"ARTICLE: RxBuddy could not find FDA label information for this drug name. This may mean the name is misspelled, it is a very new medication, or it is not available in the US. A licensed pharmacist can provide accurate information.\n"
                f"CONFIDENCE: LOW\n"
                f"SOURCES: RxNorm (rxnav.nlm.nih.gov)"
            )
            return unknown_text, [], QuestionIntent.GENERAL.value, RetrievalStatus.LABEL_NOT_FOUND.value

    intent_str = _classify_query_intent(question)
    intent_enum = QuestionIntent(intent_str) if intent_str in QuestionIntent._value2member_map_ else QuestionIntent.GENERAL

    # Flag known high-risk pairs — logged so the prompt can be informed
    risky_pair = check_high_risk_pair(drug_names)
    if risky_pair:
        logger.info("[Guardrail] Known high-risk pair detected: %s + %s", risky_pair[0], risky_pair[1])

    # Supplement HIGH_RISK_DRUGS check with drug_catalog.is_high_risk
    if intent_enum == QuestionIntent.INTERACTION:
        for dn in drug_names:
            if catalog_is_high_risk(dn):
                logger.info("[Guardrail] Catalog flags '%s' as high-risk", dn)

    # Fetch label(s) — multi-drug queries get all labels fetched in parallel
    if len(drug_names) >= 2:
        all_labels = _fetch_all_labels(drug_names)
        fda_data, raw_label = all_labels.get(drug_name, (None, None)) if drug_name else (None, None)
    else:
        all_labels = {}
        fda_data, raw_label = _fetch_fda_label_with_raw(drug_name) if drug_name else (None, None)

    # ── Retrieval Guard: refuse high-stakes questions without label source ──
    proceed, retrieval_status_enum = check_retrieval_guard(intent_enum, fda_data, drug_names, question)
    retrieval_status_str = retrieval_status_enum.value

    # Build citations from whatever label data we have (empty list when no label)
    cit_objects = build_citations(fda_data, raw_label, drug_name or "", intent_enum, fetched_at)
    citations_dicts = [c.model_dump() for c in cit_objects]

    if not proceed:
        refused = build_refused_answer(intent_enum, fetched_at)
        refused_text = (
            f"VERDICT: INSUFFICIENT_DATA\n"
            f"ANSWER: {refused.short_answer}\n"
            f"WARNING: This type of question requires an official FDA drug label — which was not available.\n"
            f"DETAILS: Dosing, contraindication, and pregnancy questions require verified label data | RxBuddy does not answer these from general knowledge | Your pharmacist or prescriber has access to complete label information\n"
            f"ACTION: {' | '.join(refused.what_to_do)}\n"
            f"ARTICLE: For questions about dosing, contraindications, and pregnancy safety, RxBuddy requires the official FDA-approved drug label as its source. Without that label, any answer would be a guess — which is unacceptable for these high-stakes topics.\n"
            f"CONFIDENCE: LOW\n"
            f"SOURCES: DailyMed (dailymed.nlm.nih.gov)"
        )
        return refused_text, [c.model_dump() for c in refused.citations], intent_str, retrieval_status_str

    # ── Deterministic lookup: skip Claude for known high-stakes pairs ─────────
    det = _lookup_deterministic(drug_names, intent_str)
    if det:
        det_verdict, det_direct = det
        logger.info("[Deterministic] Pre-resolved answer for %s: %s", drug_names, det_verdict)
        det_text = (
            f"VERDICT: {det_verdict}\n"
            f"ANSWER: {det_direct}\n"
            f"WARNING: This combination is pre-classified as {det_verdict} based on FDA label data.\n"
            f"DETAILS: This drug pair is identified in established clinical pharmacology references | FDA label data confirms the interaction risk | Individual risk may vary based on doses and patient factors\n"
            f"ACTION: Follow your prescriber's instructions | Do not change doses without consulting your provider | Read the official drug label\n"
            f"ARTICLE: This combination has been pre-classified based on FDA-approved drug label data and established clinical pharmacology. The classification reflects known interaction mechanisms and reported adverse effects in the literature.\n"
            f"CONFIDENCE: HIGH\n"
            f"SOURCES: FDA label (DailyMed) | Clinical pharmacology guidelines"
        )
        return det_text, citations_dicts, intent_str, retrieval_status_str

    medlineplus_data = _fetch_medlineplus(drug_name) if drug_name else None

    # Build grounded context — multi-drug gets cross-referenced label context
    if len(drug_names) >= 2 and all_labels:
        fda_context = _build_multi_drug_context(all_labels, question, medlineplus_data)
    else:
        fda_context = _build_fda_context(fda_data, question, medlineplus_data)

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=45.0)

        multi_drug_context = ""
        if len(drug_names) >= 2:
            multi_drug_context = f"DRUGS IN THIS QUERY: {', '.join(drug_names)}\nINTENT: {intent_str}\n\n"

        system_prompt = multi_drug_context + """You are the answer engine for RxBuddy, a clinical medication information tool.

CORE RULE — ONE DECISIVE ANSWER:
Every query gets exactly ONE clear, committed answer. No hedging. No "it depends" without a specific, factual explanation of what it depends on. Vague non-answers are medical errors.

━━━ VERDICT DEFINITIONS ━━━
AVOID         → Contraindicated, major interaction, serious harm documented in FDA label
CAUTION       → Moderate risk, requires monitoring, dose-dependent risk, or population-specific risk
CONSULT_PHARMACIST → Personal factors (kidney disease, weight, age, other meds) determine safety; not guessable
SAFE          → No clinically meaningful risk in the general population; FDA label confirms no significant interaction

VERDICT SELECTION — USE THIS EXACT PRIORITY ORDER:
1. If FDA label says "contraindicated" or "do not use" → AVOID
2. If FDA label says "may increase risk", "monitor", "use with caution" → CAUTION
3. If answer depends on a patient-specific factor we cannot know → CONSULT_PHARMACIST
4. If no meaningful interaction or risk exists per FDA data → SAFE

CONFLICT RULE — when data sources disagree, always use the MORE DANGEROUS verdict.
NEVER downgrade a CAUTION to SAFE because one source seems permissive.

━━━ DIRECT LINE RULES ━━━
The DIRECT line is the answer. It must be:
- Exactly ONE sentence
- Decisive — state the verdict plainly
- Specific to the drugs and intent in this question

BANNED from DIRECT (these phrases make the answer useless):
✗ "it depends"
✗ "generally okay" / "generally safe"
✗ "may be safe" / "could be safe" / "might be safe"
✗ "usually safe" / "typically safe" / "often safe"
✗ "in most cases" / "for most people"
✗ "be careful" (without specifics)
✗ "you should consult" (in DIRECT — belongs in DOCTOR section)

CORRECT DIRECT examples:
✓ "Use caution — regular alcohol use with acetaminophen significantly increases liver damage risk."
✓ "Do not take ibuprofen with warfarin — this combination raises serious bleeding risk."
✓ "Ibuprofen is contraindicated in the third trimester of pregnancy and should be avoided entirely."
✓ "Atorvastatin is safe to take with lisinopril — no clinically significant interaction exists."
✓ "Metformin dosing requires a pharmacist consultation because the right dose depends on your kidney function."

━━━ REQUIRED OUTPUT FORMAT ━━━
VERDICT: [AVOID / CAUTION / CONSULT_PHARMACIST / SAFE]
ANSWER: [one decisive sentence — the primary answer]
WARNING: [one sentence safety warning — omit this line entirely if SAFE with no caveats]
DETAILS: [clinical fact 1] | [clinical fact 2] | [clinical fact 3]
ACTION: [specific action 1] | [specific action 2] | [specific action 3]
ARTICLE: [1–3 sentence explanation of mechanism, clinical risk, and context]
CONFIDENCE: [HIGH / MEDIUM / LOW]
SOURCES: [FDA label | MedlinePlus | DailyMed | clinical guideline]

FORMAT RULES:
- All labels UPPERCASE exactly as shown
- No markdown, no bullet prefixes
- 2–4 items per DETAILS and ACTION, pipe-separated
- WARNING omitted (not left blank) when verdict is SAFE
- ARTICLE must explain the "why" — mechanism, drug interaction pathway, or clinical context
- CONFIDENCE = HIGH when FDA label explicitly addresses this; MEDIUM when inferred; LOW when extrapolated

EXAMPLE (interaction query):
VERDICT: CAUTION
ANSWER: Use caution when combining Tylenol and alcohol — regular drinking raises liver damage risk.
WARNING: Alcohol increases acetaminophen toxicity and may cause serious liver injury.
DETAILS: Acetaminophen is metabolized in the liver | Alcohol depletes the enzyme that safely processes it | Risk increases with regular or heavy drinking
ACTION: Limit alcohol to 1–2 drinks or less | Do not exceed 2g acetaminophen/day if drinking | Ask your pharmacist about safe dosing for your drinking pattern
ARTICLE: Both acetaminophen and alcohol are processed by the liver's cytochrome P450 enzymes. When alcohol is consumed regularly, these enzymes are induced and produce more of a toxic acetaminophen metabolite (NAPQI), which can overwhelm the liver's glutathione stores and cause hepatotoxicity.
CONFIDENCE: HIGH
SOURCES: FDA label | DailyMed

━━━ SELF-CHECK BEFORE OUTPUT ━━━
1. Is ANSWER a single decisive sentence with no banned phrases?
2. Does VERDICT match ANSWER and ARTICLE? (CAUTION explanations cannot have SAFE verdict)
3. Are all drug names from the question present in the answer?
4. Is ARTICLE grounded in a specific mechanism or FDA finding?
If any check fails → rewrite that section before outputting."""

        # ── Intent-specific grounding sentence injected into the user message ─────
        drug_list_str = ", ".join(drug_names) if drug_names else (drug_name or "the medication")
        _INTENT_TASK: dict[str, str] = {
            "what_is": (
                f"The patient asked: '{question}'\n"
                f"The drug involved is: {drug_list_str}\n"
                f"Explain WHAT {drug_list_str} IS: what medical condition(s) it treats, "
                f"how it works (mechanism), and its drug class. "
                f"Do NOT discuss side effects unless specifically asked. "
                f"Answer ONLY about {drug_list_str}. Do not answer about a different drug."
            ),
            "side_effects": (
                f"The patient asked: '{question}'\n"
                f"The drug involved is: {drug_list_str}\n"
                f"List the most important side effects of {drug_list_str}: most common first, "
                f"then most serious. Include any black box warnings. "
                f"Answer ONLY about {drug_list_str}. Do not answer about a different drug."
            ),
            "interaction": (
                f"The patient asked: '{question}'\n"
                f"The drugs involved are: {drug_list_str}\n"
                f"Explain specifically whether {drug_list_str} interact. "
                f"State: the interaction mechanism, severity, clinical significance, and what to do. "
                f"Answer ONLY about these specific drugs. Do not answer about different drugs."
            ),
            "dosing": (
                f"The patient asked: '{question}'\n"
                f"The drug involved is: {drug_list_str}\n"
                f"Provide standard dosing for {drug_list_str}: typical adult dose, frequency, "
                f"max daily dose, and any renal/hepatic adjustments from the label. "
                f"Answer ONLY about {drug_list_str}. Do not answer about a different drug."
            ),
            "safety": (
                f"The patient asked: '{question}'\n"
                f"The drug involved is: {drug_list_str}\n"
                f"Answer directly whether {drug_list_str} is safe given the specific context "
                f"in the question. State the risk level clearly — do not be vague. "
                f"Answer ONLY about {drug_list_str}. Do not answer about a different drug."
            ),
            "contraindications": (
                f"The patient asked: '{question}'\n"
                f"The drug involved is: {drug_list_str}\n"
                f"Explain the contraindications for {drug_list_str}: who should not take it and why. "
                f"Answer ONLY about {drug_list_str}. Do not answer about a different drug."
            ),
        }
        intent_task = _INTENT_TASK.get(intent_str, "")
        grounding_prefix = (
            f"{intent_task}\n\n" if intent_task else
            f"The patient asked: '{question}'\nThe drug(s) involved: {drug_list_str}\n"
            f"Answer ONLY about {drug_list_str}. Do not answer about a different drug.\n\n"
        )

        if fda_context:
            user_content = (
                f"{grounding_prefix}"
                f"FDA LABEL DATA (authoritative — use as primary source):\n"
                f"{fda_context}\n\n"
                f"QUESTION: {question}"
            )
        else:
            user_content = f"{grounding_prefix}QUESTION: {question}"

        logger.info("[Claude] Generating answer for: %.80s...", question)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=700,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        if not response.content:
            raise RuntimeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            raise RuntimeError("Claude returned an empty response.")

        answer = _truncate_words(text, 400)

        # ── Vague-phrase check: if DIRECT is still vague, force validator rewrite ──
        if _check_vague(answer):
            logger.warning("[QualityCheck] Vague DIRECT detected — sending to validator: %.80s", question)
        answer = _validate_ai_answer(question, answer)

        # ── Wrong-drug check on Claude's own output ──────────────────────────
        if drug_names and _is_wrong_drug_answer(question, answer):
            logger.warning("[QualityCheck] Claude returned wrong-drug answer for '%s' — using CONSULT fallback", question)
            answer = (
                f"VERDICT: CONSULT_PHARMACIST\n"
                f"ANSWER: We could not generate a reliable answer for this medication. Please consult a pharmacist.\n"
                f"WARNING: Our system could not verify the drug information matched your query.\n"
                f"DETAILS: Drug name could not be confirmed in the response | A pharmacist can provide accurate information\n"
                f"ACTION: Consult a licensed pharmacist | Check DailyMed at dailymed.nlm.nih.gov\n"
                f"ARTICLE: For accurate medication information, a licensed pharmacist is the best resource.\n"
                f"CONFIDENCE: LOW\n"
                f"SOURCES: Pharmacist consultation recommended"
            )

        logger.info("[Claude] Answer ready (%d words): %.200s", len(answer.split()), answer)
        _cache_set(_cache_key, answer, citations_dicts, intent_str, retrieval_status_str)
        return answer, citations_dicts, intent_str, retrieval_status_str

    except anthropic.APIError as e:
        logger.error("[Claude] API Error: %s (status=%s)", e.message, getattr(e, 'status_code', 'N/A'))
        raise RuntimeError(f"Claude API error: {e.message}")
    except Exception as e:
        logger.error("[Claude] Unexpected error: %s", str(e), exc_info=True)
        raise


# ---------- 2) Connect to PostgreSQL with SQLAlchemy ----------
engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
metadata = MetaData()

# We define the tables in code so we can query/insert easily.
# (The tables are created by `data/seed_db.py`.)
questions_table = Table(
    "questions",
    metadata,
    # These Column definitions match what we created earlier:
    # id, question, category, tags, answer, created_at
    # NOTE: We don't call create_all() here; we just map the table.
    # If the table doesn't exist in Postgres, seeding needs to run first.
    #
    # SQLAlchemy needs column objects; Table(..., autoload_with=engine) would also work,
    # but we keep it explicit for beginners.
    #
    # (Using dialect types is okay with Postgres.)
    #
    # Primary key:
    # - id: integer
    #
    # Data:
    # - question: text
    # - category: varchar(50)
    # - tags: text[]
    # - answer: text (nullable for now)
    # - created_at: timestamptz
    #
    # We set extend_existing=True so it won’t crash if imported twice.
    # (Example: autoreload in development.)
    #
    # IMPORTANT: These definitions do not change your DB schema by themselves.
    #
    # Columns:
    # pylint: disable=too-many-function-args
    # (Cursor/linters may not be present; this is fine.)
    #
    # Use SQLAlchemy core columns:
    # (We import types above; columns are created implicitly by Column objects.)
    #
    # We’ll declare columns via sqlalchemy.Column to be explicit.
    extend_existing=True,
)

search_logs_table = Table(
    "search_logs",
    metadata,
    extend_existing=True,
)

# Define columns (explicitly) after table creation to keep imports minimal.
from sqlalchemy import Column, ForeignKey

questions_table.append_column(Column("id", Integer, primary_key=True))
questions_table.append_column(Column("question", Text, nullable=False))
questions_table.append_column(Column("category", VARCHAR(50), nullable=True))
questions_table.append_column(Column("tags", ARRAY(Text), nullable=True))
questions_table.append_column(Column("answer", Text, nullable=True))
questions_table.append_column(Column("created_at", DateTime(timezone=True), nullable=True))

search_logs_table.append_column(Column("id", Integer, primary_key=True))
search_logs_table.append_column(Column("query", Text, nullable=False))
search_logs_table.append_column(
    Column("matched_question_id", Integer, ForeignKey("questions.id"), nullable=True)
)
search_logs_table.append_column(Column("clicked", Boolean, nullable=False, default=False))
search_logs_table.append_column(Column("session_id", VARCHAR(100), nullable=True))
search_logs_table.append_column(Column("searched_at", DateTime(timezone=True), nullable=False))


# ---------- 3) FastAPI app ----------
app = FastAPI(title="RxBuddy API", version="0.1.0")

# Allow the frontend (Vercel + local dev) to call the API from the browser.
# - Local dev: http://localhost:3000
# - Vercel preview/prod: https://*.vercel.app (or your custom domain)
cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
cors_origins += ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all: return structured JSON instead of a 500 HTML page.
    The frontend can always parse this response.
    """
    logger.error("[App] Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "query": "",
            "results": [{
                "id": 0,
                "question": "",
                "category": "General",
                "tags": [],
                "score": 0.0,
                "answer": "An unexpected error occurred. Please try again.",
                "structured": {
                    "verdict": "CONSULT_PHARMACIST",
                    "direct": "We encountered an error processing your request. Please try again or consult a pharmacist.",
                    "do": [],
                    "avoid": [],
                    "doctor": [],
                    "raw": "",
                    "confidence": "LOW",
                    "sources": "",
                    "interaction_summary": {"avoid_pairs": [], "caution_pairs": []},
                    "citations": [],
                    "intent": "general",
                    "retrieval_status": "LABEL_NOT_FOUND",
                },
            }],
            "did_you_mean": None,
            "source": "error",
            "saved_to_db": False,
        },
    )


@app.on_event("startup")
def startup_event() -> None:
    """Initialize spell checker and start keep-alive pinger on startup."""
    _init_spell_checker()
    try:
        import keep_alive
        keep_alive.start()
    except Exception as _ka_exc:
        logger.warning("[KeepAlive] Could not start keep-alive pinger: %s", _ka_exc)


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "RxBuddy is live"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- 4) Request/response models ----------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="What the user typed or spoke")
    engine: str = Field(
        "tfidf",
        description="Which search engine to use: 'tfidf' (fast) or 'knn' (transformer embeddings).",
    )
    top_k: int = Field(5, ge=1, le=10, description="How many results to return (1 to 10).")


class StructuredAnswer(BaseModel):
    """Parsed structured answer with specific bullets for each section."""
    verdict: str = "CONSULT_PHARMACIST"  # SAFE, AVOID, CAUTION, CONSULT_PHARMACIST, INSUFFICIENT_DATA
    # ── 5-section response schema ──────────────────────────────────────────────
    answer: str = ""    # 1. Primary answer — one decisive sentence
    warning: str = ""   # 2. Safety warning (empty when SAFE)
    details: list[str] = []  # 3. Clinical fact bullets
    action: list[str] = []   # 4. What to do bullets
    article: str = ""   # 5. Mini article — mechanism / context paragraph
    # ── Legacy fields (kept for backward compat with old DB rows) ───────────
    direct: str = ""    # alias for answer (populated from DIRECT: or ANSWER:)
    do: list[str] = []
    avoid: list[str] = []
    doctor: list[str] = []
    raw: str = ""
    confidence: str = "MEDIUM"
    sources: str = ""
    interaction_summary: dict[str, list[str]] = Field(
        default_factory=lambda: {"avoid_pairs": [], "caution_pairs": []}
    )
    # Answer Engine v2 fields — populated when FDA label data is retrieved
    citations: list[dict] = Field(default_factory=list)
    intent: str = "general"
    retrieval_status: str = "LABEL_NOT_FOUND"


DOSAGE_TERMS = (
    "dosage", "dose", "how much", "how many", "how to take",
    "when to take", "maximum dose", "max dose", "mg", "milligram",
    "dosing", "strength", "how often",
)

SIDE_EFFECT_TERMS = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "reaction", "reactions", "symptom", "symptoms",
)

INFORMATIONAL_TERMS = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "what is", "what are", "how does", "explain", "reaction", "reactions",
)

AVOID_PHRASES = (
    "avoid taking", "do not take", "not recommended", "bleeding risk",
    "contraindicated", "should not be taken together", "dangerous combination",
    "increased risk of bleeding", "major interaction", "severe interaction",
    "serious interaction", "avoid this combination", "do not combine",
    "not safe together", "should be avoided", "high risk", "black box warning",
)

CAUTION_PHRASES = (
    "moderate interaction", "use with caution", "monitor", "monitoring",
    "may increase risk", "can increase risk", "increased risk", "kidney strain",
    "kidney stress", "renal risk", "renal impairment", "lactic acidosis risk",
    "may worsen", "can worsen", "not ideal", "be careful", "watch for side effects",
    "needs closer monitoring", "dose adjustment may be needed",
    "generally well tolerated but", "slight risk", "small risk", "rare risk",
    "may slightly increase", "not completely safe", "should be monitored",
    "mild interaction", "possible interaction",
)

SAFE_PHRASES = (
    "no known interaction", "no significant interaction", "generally safe",
    "no clinically significant interaction", "safe to take together",
    "no major interaction", "compatible together", "typically safe",
    "low interaction risk",
)

NSAID_DRUGS = {"ibuprofen", "naproxen", "aspirin"}
ANTICOAGULANT_DRUGS = {"warfarin", "heparin"}
RENAL_RISK_DRUGS = {"metformin", "lisinopril", "losartan"}

EXPLICIT_PAIR_RISKS: dict[tuple[str, str], str] = {
    ("ibuprofen", "warfarin"): "AVOID",
    ("naproxen", "warfarin"): "AVOID",
    ("aspirin", "warfarin"): "AVOID",
    ("heparin", "ibuprofen"): "AVOID",
    ("heparin", "naproxen"): "AVOID",
    ("heparin", "aspirin"): "AVOID",
    ("ibuprofen", "metformin"): "CAUTION",
    ("ibuprofen", "lisinopril"): "CAUTION",
    ("ibuprofen", "losartan"): "CAUTION",
    ("naproxen", "metformin"): "CAUTION",
    ("naproxen", "lisinopril"): "CAUTION",
    ("naproxen", "losartan"): "CAUTION",
    ("aspirin", "metformin"): "CAUTION",
    ("aspirin", "lisinopril"): "CAUTION",
    ("aspirin", "losartan"): "CAUTION",
}


def _contains_any(haystack: str, phrases: tuple[str, ...] | list[str]) -> bool:
    return any(phrase in haystack for phrase in phrases)


def _normalize_pair(drug_a: str, drug_b: str) -> tuple[str, str]:
    return tuple(sorted((drug_a.strip().lower(), drug_b.strip().lower())))


def _pair_label(pair: tuple[str, str]) -> str:
    return f"{pair[0]} + {pair[1]}"


def _evaluate_pair_interaction(drug_a: str, drug_b: str) -> str:
    """
    Deterministic backend interaction classification for a drug pair.
    Returns SAFE, CAUTION, or AVOID.
    """
    left, right = _normalize_pair(drug_a, drug_b)
    if left == right:
        return "SAFE"

    explicit = EXPLICIT_PAIR_RISKS.get((left, right))
    if explicit:
        return explicit

    pair_set = {left, right}
    if pair_set & NSAID_DRUGS and pair_set & ANTICOAGULANT_DRUGS:
        return "AVOID"
    if pair_set & NSAID_DRUGS and pair_set & RENAL_RISK_DRUGS:
        return "CAUTION"

    return "SAFE"


def _evaluate_pairwise_interactions(drugs: list[str]) -> tuple[str, dict[str, list[str]]]:
    """
    Evaluate all unique drug pairs in the query and return the aggregate risk.
    Final rule:
    - if ANY pair is AVOID => AVOID
    - elif ANY pair is CAUTION => CAUTION
    - else SAFE
    """
    unique_drugs = list(dict.fromkeys(d.strip().lower() for d in drugs if d and d.strip()))
    summary = {"avoid_pairs": [], "caution_pairs": []}

    if len(unique_drugs) < 2:
        return "CONSULT_PHARMACIST", summary

    saw_caution = False
    for i, left in enumerate(unique_drugs):
        for right in unique_drugs[i + 1:]:
            pair_verdict = _evaluate_pair_interaction(left, right)
            label = _pair_label(_normalize_pair(left, right))
            if pair_verdict == "AVOID":
                summary["avoid_pairs"].append(label)
            elif pair_verdict == "CAUTION":
                summary["caution_pairs"].append(label)
                saw_caution = True

    if summary["avoid_pairs"]:
        return "AVOID", summary
    if saw_caution:
        return "CAUTION", summary
    return "SAFE", summary


def _legacy_extract_verdict_pre_regex(text: str, question: str = "") -> str:
    """
    Robustly extract verdict from answer text.
    Always returns one of: YES, NO, MAYBE, CONSULT_PHARMACIST
    
    ISSUE 4 FIX: For informational questions (dosage, side effects, what is, how to),
    return CONSULT_PHARMACIST instead of trying to extract YES/NO.
    """
    if not text:
        return "CONSULT_PHARMACIST"

    # SAFETY OVERRIDE: dangerous combinations always return AVOID regardless of other signals
    danger_phrases = [
        "avoid taking", "do not take", "not recommended",
        "bleeding risk", "contraindicated", "should not be taken together",
        "dangerous combination", "increased risk of bleeding"
    ]
    if any(phrase in text.lower() for phrase in danger_phrases):
        return "AVOID"

    # ISSUE 4 FIX: Check if question is informational (not yes/no)
    # These questions should NOT get YES/NO verdicts
    q_lower = question.lower() if question else ""
    
    # Informational question indicators - should get CONSULT_PHARMACIST
    informational_keywords = [
        "dosage", "dose", "how much", "how many", "how to take",
        "side effects", "what is", "what are", "how does", "explain",
        "when to take", "how long", "what happens", "why does",
        "ingredients", "storage", "expire", "half-life"
    ]
    
    # Binary question indicators - can get YES/NO
    binary_keywords = [
        "can i", "is it safe", "should i", "is it okay", "is it ok",
        "can you", "will it", "does it", "are there", "is there"
    ]
    
    # If question contains informational keywords but NO binary keywords,
    # return CONSULT_PHARMACIST immediately
    has_informational = any(kw in q_lower for kw in informational_keywords)
    has_binary = any(kw in q_lower for kw in binary_keywords)
    
    if has_informational and not has_binary:
        return "CONSULT_PHARMACIST"
    
    upper_text = text.upper()
    
    # Check for explicit Answer: or Verdict: lines
    for line in text.split("\n"):
        line_upper = line.strip().upper()
        
        # Match "Answer: YES" or "Verdict: YES" patterns
        if line_upper.startswith("ANSWER:") or line_upper.startswith("VERDICT:"):
            val = line_upper.split(":", 1)[1].strip() if ":" in line_upper else ""
            
            if val.startswith("YES") or val.startswith("USUALLY YES"):
                return "SAFE"
            elif val.startswith("NO"):
                return "AVOID"
            elif val.startswith("MAYBE") or val.startswith("DEPENDS") or val.startswith("IT DEPENDS"):
                return "CAUTION"
            elif val.startswith("NEEDS REVIEW") or val.startswith("CONSULT") or val.startswith("ASK"):
                return "CONSULT_PHARMACIST"

    # Fallback: scan the full text for verdict indicators
    # Strong SAFE indicators
    if any(phrase in upper_text for phrase in [
        "YES, YOU CAN", "YES YOU CAN", "IT IS SAFE", "GENERALLY SAFE",
        "USUALLY SAFE", "TYPICALLY SAFE", "YES,", "ANSWER: YES",
        "✅ SAFE", "SAFETY LEVEL", "SAFETY LEVEL: SAFE"
    ]):
        return "SAFE"

    # Strong AVOID indicators
    if any(phrase in upper_text for phrase in [
        "NO, YOU SHOULD NOT", "NO YOU SHOULD NOT", "DO NOT TAKE",
        "NOT RECOMMENDED", "AVOID TAKING", "SHOULD NOT TAKE",
        "NO,", "ANSWER: NO", "CONTRAINDICATED",
        "❌ AVOID", "AVOID / CONTRAINDICATED"
    ]):
        return "AVOID"

    # CAUTION indicators
    if any(phrase in upper_text for phrase in [
        "DEPENDS ON", "IT DEPENDS", "CASE BY CASE", "VARIES",
        "POSSIBLY", "MIGHT BE", "COULD BE", "SOMETIMES",
        "⚠️ USE WITH CAUTION", "USE WITH CAUTION"
    ]):
        return "CAUTION"

    # Default to CONSULT_PHARMACIST if uncertain
    return "CONSULT_PHARMACIST"


_CORRUPTED_PREFIXES = (
    "** classification",
    "** answer**",
    "**:**",
    "**primary intent",
    "** ** **",
    "---",
)
_CORRUPTED_EXACT: frozenset[str] = frozenset({
    "needs review.", "**.", "** **", "n/a", "none",
    "needs review", "n/a.", "none.", "undefined", "null", ".",
})
_CORRUPTED_SUBSTRINGS = (
    "category 6", "category 5", "category 4", "category 3",
    "primary intent category",
    "needs review",
    "answer: why:",
    "intent classification",
    " --- ",
    "\n---",
    "---\n",
    "undefined",
)


def _is_corrupted_db_answer(text: str) -> bool:
    """
    Return True when a stored DB answer contains leaked internal prompt text,
    raw category labels, or is too short/malformed to be useful.
    These rows are skipped and Claude regenerates a fresh answer in their place.
    """
    if text is None:
        return True
    t = text.strip()
    if not t:
        return True
    t_lower = t.lower()
    # Minimum viable answer length
    if len(t) < 40:
        return True
    if t_lower in _CORRUPTED_EXACT:
        return True
    # Starts with a character that indicates a fragment/label leak
    if t[0] in (":", ".") or t.startswith("- ") or t.startswith("---"):
        return True
    for prefix in _CORRUPTED_PREFIXES:
        if t_lower.startswith(prefix):
            return True
    # Leaked internal content anywhere in the answer
    for sub in _CORRUPTED_SUBSTRINGS:
        if sub in t_lower:
            return True
    return False


def _is_wrong_drug_answer(query: str, answer_text: str) -> bool:
    """
    Return True when the answer text contains none of the drug names from the query.

    This catches the bug where TF-IDF retrieves an answer about a completely
    different drug (e.g. rosuvastatin answer for a risperidone query).

    Returns False (allow through) when:
    - The query contains no recognisable drug names (can't judge relevance).
    - At least one drug name or synonym from the query appears in the answer.
    """
    if not answer_text or not query:
        return False

    drug_names = _extract_drug_names(query)
    if not drug_names:
        return False  # No drugs in query — cannot judge drug relevance

    answer_lower = answer_text.lower()

    # Build the full name set: generic + brand names / synonyms from catalog
    all_names: set[str] = set()
    for drug in drug_names:
        all_names.add(drug.lower())
        try:
            rec = find_drug(drug)
            if rec:
                for bn in rec.brand_names:
                    all_names.add(bn.lower())
                for gn in rec.generic_names:
                    all_names.add(gn.lower())
        except Exception:
            pass

    # Allow through if ANY drug name (≥4 chars) appears in the answer
    for name in all_names:
        if len(name) >= 4 and name in answer_lower:
            return False

    return True  # No query drug found in answer → wrong drug, reject


def _post_process_cached_answer(answer_text: str) -> str:
    """
    Post-process cached answers to clean up raw markdown bullets
    and preserve section headers as bold labels.
    
    BUG 2 FIX: Also handles inline dash format like:
    "What to do: - item1 - item2 What to avoid: - item3"
    
    ISSUE 2 FIX: Filters out leaked Claude reasoning/system prompt text.
    """
    if not answer_text:
        return answer_text
    
    text = answer_text
    
    # ISSUE 2 FIX: Remove leaked Claude reasoning and system prompt fragments
    # These patterns indicate corrupted DB entries with exposed internal prompts
    leaked_patterns = [
        r"STEP\s*\d+[-:]?\d*\s*[:—-]?",  # "STEP 1-3:", "STEP 1:", "STEP 2"
        r"INTENT\s*CLASSIFICATION",
        r"Primary\s*Intent\s*:",
        r"\*\*Primary\s*Intent\s*:",
        r"NEEDS\s*REVIEW\s*\*\*",
        r"CROSS-EXAMINATION\s*CHECK",
        r"CONTRADICTION\s*BLOCK",
        r"SIMPLICITY\s*RULE",
        r"FINAL\s*SELF-AUDIT",
        r"MISMATCH\s*PREVENTION",
        r"SAFETY\s*FILTER",
        r"DO\s*NOT\s*HALLUCINATE",
        r"VALID\s*INTENT\s*CATEGORIES",
    ]
    
    for pattern in leaked_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    
    # Also remove lines that are entirely system prompt artifacts
    lines = text.split("\n")
    filtered_lines = []
    for line in lines:
        line_upper = line.strip().upper()
        # Skip lines that look like system prompt instructions
        if any(skip in line_upper for skip in [
            "STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5",
            "STEP 6", "STEP 7", "STEP 8", "STEP 9", "STEP 10", "STEP 11",
            "INTENT CLASSIFICATION", "PRIMARY INTENT:",
            "READ THE FULL QUESTION", "EXTRACT THE CORE ASK",
            "ANSWER THE EXACT QUESTION", "CROSS-EXAMINATION",
            "CONTRADICTION BLOCK", "SIMPLICITY RULE",
        ]):
            continue
        filtered_lines.append(line)
    
    text = "\n".join(filtered_lines)
    
    # BUG 2 FIX: First, handle inline dash lists
    # Pattern: "Header: - item - item - item NextHeader:" or end of string
    # Common headers to look for
    section_headers = [
        "What to do", "What to avoid", "See a doctor if", "Get medical help",
        "Important notes", "Important note", "Warning", "Warnings",
        "Do", "Avoid", "Doctor", "Answer", "Why", "Verdict"
    ]
    
    # Check if text contains inline dash pattern (header followed by dashes without newlines)
    if " - " in text and not "\n- " in text:
        # This looks like an inline format, let's reformat it
        result_lines = []
        remaining = text
        
        while remaining:
            # Find the next section header
            best_match = None
            best_pos = len(remaining)
            
            for header in section_headers:
                # Look for "Header:" pattern
                patterns = [f"{header}:", f"{header.lower()}:", f"{header.upper()}:"]
                for pattern in patterns:
                    pos = remaining.find(pattern)
                    if pos != -1 and pos < best_pos:
                        best_pos = pos
                        best_match = (pos, len(pattern), header)
            
            if best_match:
                pos, pattern_len, header = best_match
                
                # Add any text before this header
                if pos > 0:
                    prefix = remaining[:pos].strip()
                    if prefix:
                        result_lines.append(prefix)
                
                # Find where this section ends (next header or end)
                section_start = pos + pattern_len
                next_header_pos = len(remaining)
                
                for next_header in section_headers:
                    for pattern in [f"{next_header}:", f"{next_header.lower()}:", f"{next_header.upper()}:"]:
                        next_pos = remaining.find(pattern, section_start)
                        if next_pos != -1 and next_pos < next_header_pos:
                            next_header_pos = next_pos
                
                # Extract this section's content
                section_content = remaining[section_start:next_header_pos].strip()
                
                # Add the header as bold
                result_lines.append(f"\n**{header}:**")
                
                # Split content by " - " and make bullet points
                if " - " in section_content:
                    items = section_content.split(" - ")
                    for item in items:
                        item = item.strip()
                        if item:
                            result_lines.append(f"- {item}")
                elif section_content:
                    result_lines.append(section_content)
                
                remaining = remaining[next_header_pos:]
            else:
                # No more headers, add remaining text
                if remaining.strip():
                    result_lines.append(remaining.strip())
                break
        
        text = "\n".join(result_lines)
    
    # Now process line by line for any remaining formatting
    lines = text.split("\n")
    processed_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines but preserve spacing
        if not stripped:
            processed_lines.append("")
            continue
        
        # Convert section headers (lines ending with :) to bold if not already bold
        if stripped.endswith(":") and len(stripped) < 50 and not stripped.startswith("**"):
            processed_lines.append(f"**{stripped}**")
            continue
        
        # Clean up raw bullet points - convert to proper list format
        if stripped.startswith("- ") or stripped.startswith("• ") or stripped.startswith("* "):
            # Keep as-is for markdown rendering
            processed_lines.append(stripped)
            continue
        
        # Check for numbered lists
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)" :
            processed_lines.append(stripped)
            continue
        
        # Regular line
        processed_lines.append(stripped)
    
    return "\n".join(processed_lines)


def _legacy_extract_verdict_pre_pairwise(text: str, question: str = "") -> str:
    """
    Robustly extract a backend verdict and keep it consistent with the explanation.
    Priority order: AVOID > CAUTION > SAFE > CONSULT_PHARMACIST.
    """
    if not text:
        return "CONSULT_PHARMACIST"

    normalized_text = text.replace("\r\n", "\n")
    lower_text = normalized_text.lower()
    q_lower = question.lower() if question else ""

    dosage_terms = [
        "dosage", "dose", "how much", "how many", "how to take",
        "when to take", "maximum dose", "max dose", "mg", "milligram",
        "dosing", "strength", "how often",
    ]
    side_effect_terms = [
        "side effect", "side effects", "adverse effect", "adverse effects",
        "reaction", "reactions", "symptom", "symptoms",
    ]
    avoid_phrases = [
        "avoid taking", "do not take", "not recommended", "bleeding risk",
        "contraindicated", "should not be taken together", "dangerous combination",
        "increased risk of bleeding", "major interaction", "severe interaction",
        "serious interaction", "avoid this combination", "do not combine",
        "not safe together", "should be avoided", "high risk", "black box warning",
    ]
    caution_phrases = [
        "moderate interaction", "use with caution", "monitor", "monitoring",
        "may increase risk", "can increase risk", "increased risk", "kidney strain",
        "kidney stress", "renal risk", "renal impairment", "lactic acidosis risk",
        "may worsen", "can worsen", "not ideal", "be careful", "watch for side effects",
        "needs closer monitoring", "dose adjustment may be needed",
    ]
    safe_phrases = [
        "no known interaction", "no significant interaction", "generally safe",
        "no clinically significant interaction", "safe to take together",
        "no major interaction", "compatible together", "typically safe",
        "low interaction risk",
    ]

    def _contains_any(haystack: str, phrases: list[str]) -> bool:
        return any(phrase in haystack for phrase in phrases)

    def _extract_explicit_verdict(raw_text: str) -> str | None:
        for raw_line in raw_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[#*\-\s]+", "", line)
            upper_line = line.upper()
            if upper_line.startswith("ANSWER:") or upper_line.startswith("VERDICT:"):
                val = upper_line.split(":", 1)[1].strip() if ":" in upper_line else ""
                if val.startswith("SAFE") or val.startswith("YES") or val.startswith("USUALLY YES"):
                    return "SAFE"
                if val.startswith("AVOID") or val.startswith("NO"):
                    return "AVOID"
                if val.startswith("CAUTION") or val.startswith("MAYBE") or val.startswith("DEPENDS") or val.startswith("IT DEPENDS"):
                    return "CAUTION"
                if val.startswith("NEEDS REVIEW") or val.startswith("CONSULT") or val.startswith("ASK"):
                    return "CONSULT_PHARMACIST"
        return None

    # Intent override comes first so informational queries resolve consistently.
    if _contains_any(q_lower, dosage_terms):
        return "CONSULT_PHARMACIST"
    if _contains_any(q_lower, side_effect_terms):
        return "CAUTION"

    if _contains_any(lower_text, avoid_phrases):
        return "AVOID"
    if _contains_any(lower_text, caution_phrases):
        return "CAUTION"

    explicit_verdict = _extract_explicit_verdict(normalized_text)
    if explicit_verdict == "CAUTION" and _contains_any(lower_text, avoid_phrases):
        return "AVOID"
    if explicit_verdict == "SAFE":
        if _contains_any(lower_text, avoid_phrases):
            return "AVOID"
        if _contains_any(lower_text, caution_phrases):
            return "CAUTION"
        return "SAFE"
    if explicit_verdict == "AVOID":
        return "AVOID"
    if explicit_verdict == "CAUTION":
        return "CAUTION"
    if explicit_verdict == "CONSULT_PHARMACIST":
        return explicit_verdict

    upper_text = normalized_text.upper()
    has_safe_signal = _contains_any(lower_text, safe_phrases) or any(
        phrase in upper_text for phrase in [
            "YES, YOU CAN", "YES YOU CAN", "IT IS SAFE", "GENERALLY SAFE",
            "USUALLY SAFE", "TYPICALLY SAFE", "YES,", "ANSWER: YES",
            "âœ… SAFE", "SAFETY LEVEL", "SAFETY LEVEL: SAFE",
        ]
    )
    if has_safe_signal and not _contains_any(lower_text, caution_phrases) and not _contains_any(lower_text, avoid_phrases):
        return "SAFE"

    if any(phrase in upper_text for phrase in [
        "NO, YOU SHOULD NOT", "NO YOU SHOULD NOT", "DO NOT TAKE",
        "NOT RECOMMENDED", "AVOID TAKING", "SHOULD NOT TAKE",
        "NO,", "ANSWER: NO", "CONTRAINDICATED",
        "âŒ AVOID", "AVOID / CONTRAINDICATED",
    ]):
        return "AVOID"

    if any(phrase in upper_text for phrase in [
        "DEPENDS ON", "IT DEPENDS", "CASE BY CASE", "VARIES",
        "POSSIBLY", "MIGHT BE", "COULD BE", "SOMETIMES",
        "âš ï¸ USE WITH CAUTION", "USE WITH CAUTION",
    ]):
        return "CAUTION"

    return "CONSULT_PHARMACIST"


def _parse_structured_answer(answer_text: str, question: str = "") -> StructuredAnswer:
    """
    Parse Claude output into StructuredAnswer using regex-based extraction.
    Handles minor formatting drift and never raises on malformed model output.
    """
    result = StructuredAnswer(raw=answer_text, verdict="CONSULT_PHARMACIST")

    if not answer_text:
        return result

    try:
        text = _post_process_cached_answer(answer_text.strip())
        result.raw = text

        def _normalize_verdict(value: str) -> str:
            upper = value.strip().upper().replace("-", " ").replace("_", " ")
            upper = re.sub(r"\s+", " ", upper)
            if upper.startswith("AVOID") or upper.startswith("NO"):
                return "AVOID"
            if upper.startswith("CAUTION") or upper.startswith("MAYBE") or upper.startswith("DEPENDS"):
                return "CAUTION"
            if upper.startswith("SAFE") or upper.startswith("YES") or upper.startswith("USUALLY YES"):
                return "SAFE"
            if upper.startswith("CONSULT") or upper.startswith("NEEDS REVIEW") or upper.startswith("ASK"):
                return "CONSULT_PHARMACIST"
            return ""

        def _extract_field(patterns: list[str]) -> str:
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
                if match:
                    value = (match.group(1) or "").strip()
                    if value:
                        return value
            return ""

        def _split_items(value: str) -> list[str]:
            if not value:
                return []
            normalized = value.replace("\r\n", "\n").strip()
            parts = [normalized]
            if "|" in normalized:
                parts = normalized.split("|")
            elif "\n" in normalized:
                parts = normalized.split("\n")
            cleaned: list[str] = []
            for part in parts:
                item = re.sub(r"^\s*[-*•]+\s*", "", part).strip(" .;")
                if item:
                    cleaned.append(item)
            deduped: list[str] = []
            for item in cleaned:
                if item not in deduped:
                    deduped.append(item)
            return deduped

        label_boundary = r"(?=^\s*(?:VERDICT|ANSWER|DIRECT|WHY|DO|AVOID|DOCTOR|WARNING|DETAILS|ACTION|ARTICLE|GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF|SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?|CONFIDENCE|SOURCES)\s*[:\-]|\Z)"

        verdict_raw = _extract_field([
            rf"^\s*VERDICT\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        result.verdict = _normalize_verdict(verdict_raw) or _extract_verdict(text, question)

        # New 5-section fields (preferred)
        answer_raw = _extract_field([
            rf"^\s*ANSWER\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DIRECT\s*[:\-]\s*(.+?)\s*{label_boundary}",  # legacy fallback
        ])
        warning_raw = _extract_field([
            rf"^\s*WARNING\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DOCTOR\s*[:\-]\s*(.+?)\s*{label_boundary}",  # legacy fallback
        ])
        details_raw = _extract_field([
            rf"^\s*DETAILS\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        action_raw = _extract_field([
            rf"^\s*ACTION\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DO\s*[:\-]\s*(.+?)\s*{label_boundary}",  # legacy fallback
        ])
        article_raw = _extract_field([
            rf"^\s*ARTICLE\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*WHY\s*[:\-]\s*(.+?)\s*{label_boundary}",  # legacy fallback
            rf"^\s*REASON\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        # Legacy fields (for old DB rows still using old format)
        do_raw = _extract_field([
            rf"^\s*DO\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        avoid_raw = _extract_field([
            rf"^\s*AVOID\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        doctor_raw = _extract_field([
            rf"^\s*DOCTOR\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        confidence_raw = _extract_field([
            rf"^\s*CONFIDENCE\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        sources_raw = _extract_field([
            rf"^\s*SOURCES?\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])

        # Populate new fields
        result.answer = answer_raw or ""
        result.direct = answer_raw or ""  # keep in sync for legacy consumers
        result.warning = warning_raw or ""
        result.details = _split_items(details_raw)
        result.action = _split_items(action_raw)
        result.article = article_raw or ""
        # Legacy fields
        result.do = _split_items(do_raw or action_raw)
        result.avoid = _split_items(avoid_raw)
        result.doctor = _split_items(doctor_raw)
        result.sources = sources_raw or ""

        conf = confidence_raw.strip().upper()
        if conf in ("HIGH", "MEDIUM", "LOW"):
            result.confidence = conf
        elif "HIGH" in conf:
            result.confidence = "HIGH"
        elif "LOW" in conf:
            result.confidence = "LOW"
        else:
            result.confidence = "MEDIUM"

        if not result.answer:
            sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " ").strip())
            if sentences and sentences[0]:
                result.answer = sentences[0].strip()
                result.direct = result.answer

        if result.answer and not re.search(r"[.!?]$", result.answer):
            result.answer += "."
            result.direct = result.answer

        result.interaction_summary = build_interaction_summary(question)
        result.verdict = validate_and_correct_verdict(
            text,
            result.verdict,
            question,
            interaction_summary=result.interaction_summary,
        )
        result = _format_structured_answer_for_ui(result, question)

        return result

    except Exception:
        fallback_text = answer_text.strip()
        result.raw = fallback_text
        result.verdict = _extract_verdict(fallback_text, question)
        sentences = re.split(r"(?<=[.!?])\s+", fallback_text.replace("\n", " ").strip())
        if sentences and sentences[0]:
            result.direct = sentences[0].strip()
            if result.direct and not re.search(r"[.!?]$", result.direct):
                result.direct += "."
        result.do = []
        result.avoid = []
        result.doctor = []
        result.confidence = "MEDIUM"
        result.sources = ""
        result.interaction_summary = build_interaction_summary(question)
        result.verdict = validate_and_correct_verdict(
            fallback_text,
            result.verdict,
            question,
            interaction_summary=result.interaction_summary,
        )
        result = _format_structured_answer_for_ui(result, question)
        return result


def _legacy_validate_and_correct_verdict(answer_text: str, verdict: str, question: str = "") -> str:
    """
    Final backend authority for verdict correction.
    Uses deterministic rules to reconcile the returned verdict with the explanation text.
    Priority order: AVOID > CAUTION > SAFE > CONSULT_PHARMACIST.
    """
    normalized_verdict = (verdict or "").strip().upper() or "CONSULT_PHARMACIST"
    combined_text = f"{question}\n{answer_text}".lower()

    dosage_terms = [
        "dosage", "dose", "how much", "how many", "how to take",
        "when to take", "maximum dose", "max dose", "mg", "milligram",
        "dosing", "strength", "how often",
    ]
    informational_terms = [
        "side effect", "side effects", "adverse effect", "adverse effects",
        "what is", "what are", "how does", "explain", "reaction", "reactions",
    ]
    avoid_phrases = [
        "avoid taking", "do not take", "not recommended", "bleeding risk",
        "contraindicated", "should not be taken together", "dangerous combination",
        "increased risk of bleeding", "major interaction", "severe interaction",
        "serious interaction", "avoid this combination", "do not combine",
        "not safe together", "should be avoided", "high risk", "black box warning",
    ]
    caution_phrases = [
        "moderate interaction", "use with caution", "monitor", "monitoring",
        "may increase risk", "can increase risk", "increased risk", "kidney strain",
        "kidney stress", "renal risk", "renal impairment", "lactic acidosis risk",
        "may worsen", "can worsen", "not ideal", "be careful", "watch for side effects",
        "needs closer monitoring", "dose adjustment may be needed",
    ]
    safe_phrases = [
        "no known interaction", "no significant interaction", "generally safe",
        "no clinically significant interaction", "safe to take together",
        "no major interaction", "compatible together", "typically safe",
        "low interaction risk",
    ]

    def _contains_any(haystack: str, phrases: list[str]) -> bool:
        return any(phrase in haystack for phrase in phrases)

    if _contains_any(combined_text, dosage_terms):
        return "CONSULT_PHARMACIST"
    if _contains_any(combined_text, avoid_phrases):
        return "AVOID"
    if _contains_any(combined_text, caution_phrases):
        return "CAUTION"
    if _contains_any(combined_text, informational_terms):
        return "CAUTION"
    if normalized_verdict == "SAFE" and _contains_any(combined_text, safe_phrases):
        return "SAFE"
    if normalized_verdict == "SAFE":
        return "CONSULT_PHARMACIST"
    if normalized_verdict in {"AVOID", "CAUTION", "CONSULT_PHARMACIST"}:
        return normalized_verdict
    if _contains_any(combined_text, safe_phrases):
        return "SAFE"
    return "CONSULT_PHARMACIST"


def build_interaction_summary(question: str) -> dict[str, list[str]]:
    drugs = _extract_drug_names(question)
    if len(drugs) < 2:
        q_lower = question.lower()
        for known in _ALL_KNOWN_DRUGS:
            if known in q_lower and known not in drugs:
                drugs.append(known)
    _, summary = _evaluate_pairwise_interactions(list(dict.fromkeys(drugs)))
    return summary


def _format_structured_answer_for_ui(structured: StructuredAnswer, question: str = "") -> StructuredAnswer:
    """
    Remove generic noise and keep the response focused for the UI.
    Output target:
    - verdict
    - 1-2 sentence explanation
    - 2-4 relevant bullet points max
    """
    generic_noise = (
        "consult your pharmacist",
        "ask your pharmacist",
        "talk to your doctor",
        "ask your doctor",
        "for emergencies",
        "seek medical attention immediately",
        "always read the label",
        "follow package directions",
        "follow the package directions",
        "use the lowest effective dose",
        "keep out of reach of children",
    )

    def _is_relevant(item: str) -> bool:
        lower = item.lower().strip()
        if not lower:
            return False
        return not any(phrase in lower for phrase in generic_noise)

    def _limit(items: list[str], count: int) -> list[str]:
        filtered = [item.strip() for item in items if _is_relevant(item)]
        deduped: list[str] = []
        for item in filtered:
            if item not in deduped:
                deduped.append(item)
        return deduped[:count]

    sentences = re.split(r"(?<=[.!?])\s+", (structured.direct or "").strip())
    structured.direct = " ".join([s.strip() for s in sentences if s.strip()][:2]).strip()

    structured.do = _limit(structured.do, 2)
    structured.avoid = _limit(structured.avoid, 1)
    structured.doctor = _limit(structured.doctor, 1)
    return structured


def _extract_verdict(text: str, question: str = "") -> str:
    """
    Final deterministic backend verdict extraction.
    Priority order: AVOID > CAUTION > SAFE > CONSULT_PHARMACIST.
    """
    if not text:
        return "CONSULT_PHARMACIST"

    normalized_text = text.replace("\r\n", "\n")
    lower_text = normalized_text.lower()
    q_lower = question.lower() if question else ""
    query_intent = _classify_query_intent(question)

    def _extract_explicit_verdict(raw_text: str) -> str | None:
        for raw_line in raw_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[#*\-\s]+", "", line)
            upper_line = line.upper()
            if upper_line.startswith("ANSWER:") or upper_line.startswith("VERDICT:"):
                val = upper_line.split(":", 1)[1].strip() if ":" in upper_line else ""
                if val.startswith("SAFE") or val.startswith("YES") or val.startswith("USUALLY YES"):
                    return "SAFE"
                if val.startswith("AVOID") or val.startswith("NO"):
                    return "AVOID"
                if val.startswith("CAUTION") or val.startswith("MAYBE") or val.startswith("DEPENDS") or val.startswith("IT DEPENDS"):
                    return "CAUTION"
                if val.startswith("NEEDS REVIEW") or val.startswith("CONSULT") or val.startswith("ASK"):
                    return "CONSULT_PHARMACIST"
        return None

    if query_intent == "dosing" or _contains_any(q_lower, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if query_intent == "side_effects" or _contains_any(q_lower, SIDE_EFFECT_TERMS):
        return "CAUTION"
    if query_intent == "safety_general" and ("water" in q_lower or "food" in q_lower):
        if not _contains_any(lower_text, AVOID_PHRASES) and not _contains_any(lower_text, CAUTION_PHRASES):
            return "SAFE"

    pairwise_verdict = "CONSULT_PHARMACIST"
    if query_intent == "interaction":
        drugs = _extract_drug_names(question)
        if len(drugs) < 2:
            for known in _ALL_KNOWN_DRUGS:
                if known in q_lower and known not in drugs:
                    drugs.append(known)
        drugs = list(dict.fromkeys(drugs))
        pairwise_verdict, _ = _evaluate_pairwise_interactions(drugs)
        if pairwise_verdict == "AVOID":
            return "AVOID"
        if pairwise_verdict == "CAUTION":
            return "CAUTION"

    if _contains_any(lower_text, AVOID_PHRASES):
        return "AVOID"
    if _contains_any(lower_text, CAUTION_PHRASES):
        return "CAUTION"

    explicit_verdict = _extract_explicit_verdict(normalized_text)
    if explicit_verdict == "CAUTION" and _contains_any(lower_text, AVOID_PHRASES):
        return "AVOID"
    if explicit_verdict == "SAFE":
        if _contains_any(lower_text, AVOID_PHRASES):
            return "AVOID"
        if _contains_any(lower_text, CAUTION_PHRASES):
            return "CAUTION"
        return "SAFE"
    if explicit_verdict in {"AVOID", "CAUTION", "CONSULT_PHARMACIST"}:
        return explicit_verdict

    upper_text = normalized_text.upper()
    has_safe_signal = _contains_any(lower_text, SAFE_PHRASES) or any(
        phrase in upper_text for phrase in (
            "YES, YOU CAN", "YES YOU CAN", "IT IS SAFE", "GENERALLY SAFE",
            "USUALLY SAFE", "TYPICALLY SAFE", "YES,", "ANSWER: YES",
            "Ã¢Å“â€¦ SAFE", "SAFETY LEVEL", "SAFETY LEVEL: SAFE",
        )
    )
    if has_safe_signal and not _contains_any(lower_text, CAUTION_PHRASES) and not _contains_any(lower_text, AVOID_PHRASES):
        return "SAFE"

    if any(phrase in upper_text for phrase in (
        "NO, YOU SHOULD NOT", "NO YOU SHOULD NOT", "DO NOT TAKE",
        "NOT RECOMMENDED", "AVOID TAKING", "SHOULD NOT TAKE",
        "NO,", "ANSWER: NO", "CONTRAINDICATED",
        "Ã¢ÂÅ’ AVOID", "AVOID / CONTRAINDICATED",
    )):
        return "AVOID"

    if any(phrase in upper_text for phrase in (
        "DEPENDS ON", "IT DEPENDS", "CASE BY CASE", "VARIES",
        "POSSIBLY", "MIGHT BE", "COULD BE", "SOMETIMES",
        "Ã¢Å¡Â Ã¯Â¸Â USE WITH CAUTION", "USE WITH CAUTION",
    )):
        return "CAUTION"

    if query_intent == "interaction" and pairwise_verdict == "SAFE":
        if not _contains_any(lower_text, CAUTION_PHRASES) and not _contains_any(lower_text, AVOID_PHRASES):
            return "SAFE"

    return "CONSULT_PHARMACIST"


def validate_and_correct_verdict(
    answer_text: str,
    verdict: str,
    question: str = "",
    interaction_summary: dict[str, list[str]] | None = None,
) -> str:
    """
    Final backend authority for verdict correction.
    Deterministically reconciles explanation text, intent, and pairwise interaction risk.
    """
    normalized_verdict = (verdict or "").strip().upper() or "CONSULT_PHARMACIST"
    combined_text = f"{question}\n{answer_text}".lower()
    summary = interaction_summary or build_interaction_summary(question)
    query_intent = _classify_query_intent(question)

    if query_intent == "dosing" or _contains_any(combined_text, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if query_intent == "interaction" and summary.get("avoid_pairs"):
        return "AVOID"
    if _contains_any(combined_text, AVOID_PHRASES):
        return "AVOID"
    if query_intent == "interaction" and summary.get("caution_pairs"):
        return "CAUTION"
    if _contains_any(combined_text, CAUTION_PHRASES):
        return "CAUTION"
    if query_intent == "side_effects" or _contains_any(combined_text, INFORMATIONAL_TERMS):
        return "CAUTION"
    if query_intent == "safety_general" and ("water" in combined_text or "food" in combined_text):
        if not _contains_any(combined_text, CAUTION_PHRASES) and not _contains_any(combined_text, AVOID_PHRASES):
            return "SAFE"
    if normalized_verdict == "SAFE" and _contains_any(combined_text, SAFE_PHRASES):
        return "SAFE"
    if normalized_verdict == "SAFE" and _contains_any(combined_text, CAUTION_PHRASES + AVOID_PHRASES):
        return "CAUTION"
    if normalized_verdict == "SAFE" and not _contains_any(combined_text, CAUTION_PHRASES) and not _contains_any(combined_text, AVOID_PHRASES):
        return "SAFE"
    if normalized_verdict in {"AVOID", "CAUTION", "CONSULT_PHARMACIST"}:
        return normalized_verdict
    if _contains_any(combined_text, SAFE_PHRASES):
        return "SAFE"
    return "CONSULT_PHARMACIST"


class QuestionMatch(BaseModel):
    id: int
    question: str
    category: Optional[str] = None
    tags: list[str] = []
    score: Optional[float] = None
    answer: Optional[str] = None
    structured: Optional[StructuredAnswer] = None  # Parsed structured answer


class SearchResponse(BaseModel):
    query: str
    results: list[QuestionMatch]
    did_you_mean: Optional[str] = None  # Spell check suggestion (only if something changed)
    source: str = "database"  # "database" or "ai_generated"
    saved_to_db: bool = False  # True if AI-generated question was saved to database


class LogRequest(BaseModel):
    query: str = Field(..., min_length=1)
    matched_question_id: Optional[int] = None
    clicked: bool = False
    session_id: Optional[str] = None


class LogResponse(BaseModel):
    ok: bool
    log_id: int


# ---------- 4b) AI answer endpoint ----------
class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Patient question to answer")


class AnswerResponse(BaseModel):
    question: str
    answer: str


@app.post("/answer", response_model=AnswerResponse)
def answer(req: AnswerRequest) -> AnswerResponse:
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="question cannot be empty")

    try:
        a, _, _, _ = _generate_ai_answer(q)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Claude error while generating answer. Make sure ANTHROPIC_API_KEY is set. ({e})",
        )

    return AnswerResponse(question=q, answer=a)


# ---------- 5) ML search ----------
# TF-IDF search is a great baseline: fast, simple, and works well on FAQs.
from ml.tfidf_search import search as tfidf_search, find_exact_match, rebuild_index as rebuild_tfidf_index
from ml.knn_search import search as knn_search

# Near-exact match threshold (95% similarity = treat as same question)
NEAR_EXACT_THRESHOLD = 0.95


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    """
    Takes a user query and returns the top 5 matching questions.

    How it works:
    1) Check for exact/near-exact match FIRST (instant return if found)
    2) Normalize query (fix misspellings, slang, filler words) — zero API calls
    3) Run spell check and generate "did you mean?" suggestion
    4) Use TF-IDF + cosine similarity to find the best matching question IDs
    5) Check confidence threshold:
       - If best score >= 0.35 → return database results (source="database")
       - If best score < 0.35 → generate live Claude answer (source="ai_generated")
    6) Self-learning: if AI-generated and valid pharmacy question, save to DB + rebuild index
    """
    user_query = req.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # ── Drug normalization (Phase 6 wire-in) ─────────────────────────────────
    # Resolve drug names before any search so the rest of the pipeline works
    # with canonical generic names regardless of how the user spelled them.
    # If the query names an entirely unrecognisable substance (not a known
    # drug, not resolvable via RxNorm) and no other drugs are found, return
    # a CONSULT response immediately without hitting the DB or Claude.
    try:
        from services.drug_resolver import resolve_query_drugs
        _resolved_drugs = resolve_query_drugs(user_query)
        if _resolved_drugs:
            # Rewrite query tokens: replace misspelled/brand names with generics
            _rewritten = user_query
            for _rd in _resolved_drugs:
                _inp = _rd.get("input", "")
                _gen = _rd.get("generic", "")
                _cor = _rd.get("corrected", "")
                # Replace corrected spelling if it differs from the original input
                if _cor and _cor.lower() != _inp.lower() and _cor.lower() in _rewritten.lower():
                    _rewritten = re.sub(
                        re.escape(_cor), _gen, _rewritten, flags=re.IGNORECASE
                    )
            if _rewritten != user_query:
                logger.info(
                    "[DrugResolver] Rewrote query: '%s' → '%s'", user_query, _rewritten
                )
                user_query = _rewritten
    except Exception as _dr_exc:
        logger.debug("[DrugResolver] Skipped (error): %s", _dr_exc)

    logger.info("[Search] Checking exact match for: %.80s", user_query)

    # STEP 1: Check for exact match FIRST (before any processing)
    exact_match = find_exact_match(user_query)
    if exact_match and exact_match.answer:
        if _is_corrupted_db_answer(exact_match.answer):
            logger.warning("[DB] Corrupted answer in exact match Q#%d — bypassing to Claude", exact_match.id)
        elif _is_wrong_drug_answer(user_query, exact_match.answer):
            logger.warning("[DB] Wrong-drug answer in exact match Q#%d — bypassing to Claude", exact_match.id)
        else:
            logger.info("[Search] Exact match found: Q#%d — returning from database", exact_match.id)
            _log_search(user_query, exact_match.id)
            return SearchResponse(
                query=user_query,
                results=[
                    QuestionMatch(
                        id=exact_match.id,
                        question=exact_match.question,
                        category=exact_match.category,
                        tags=exact_match.tags,
                        score=1.0,
                        answer=exact_match.answer,
                        structured=_parse_structured_answer(exact_match.answer, exact_match.question),
                    )
                ],
                did_you_mean=None,
                source="database",
                saved_to_db=False,
            )

    logger.info("[Search] No exact match — running TF-IDF search")

    original_query, cleaned_query = normalize_query(user_query)
    search_query = cleaned_query if cleaned_query else user_query

    # Run spell check on the cleaned query
    did_you_mean = spell_check_query(search_query)

    engine_name = (req.engine or "tfidf").strip().lower()
    top_k = int(req.top_k)

    # Extract intent + drugs BEFORE search so we can build a better query
    drug_names = _extract_drug_names(search_query)
    query_intent = _classify_query_intent(search_query)
    enhanced_query = _build_intent_query(search_query, query_intent, drug_names)

    if engine_name == "tfidf":
        matches = tfidf_search(search_query, top_k=top_k)

        # Run a second pass with the intent-enhanced query and merge results
        if enhanced_query != search_query:
            extra = tfidf_search(enhanced_query, top_k=top_k)
            seen: dict[int, object] = {m.id: m for m in matches}
            for m in extra:
                if m.id not in seen or float(m.score) > float(seen[m.id].score):  # type: ignore[union-attr]
                    seen[m.id] = m
            matches = list(seen.values())

        # Re-rank by intent + drug overlap before returning results
        matches = _rerank_by_intent(matches, drug_names, query_intent)
        match_ids = [m.id for m in matches]
        score_by_id = {m.id: float(m.score) for m in matches}
        answer_by_id = {m.id: m.answer for m in matches}

        # Relevance guard: the best DB match must contain every drug name from the user query
        # AND every key concept word (alcohol, pregnant, liver, etc.) present in the query.
        # Without this, "tylenol and alcohol" returns the cached "Tylenol on an empty stomach"
        # answer because TF-IDF scores it at 0.46 — above the 0.35 confidence threshold.
        if matches:
            best_q_lower = (matches[0].question or "").lower()
            user_q_lower = search_query.lower()
            missing_drugs    = [d for d in drug_names if d not in best_q_lower]
            query_concepts   = [c for c in _REQUIRED_CONCEPT_WORDS if c in user_q_lower]
            missing_concepts = [c for c in query_concepts if c not in best_q_lower]
            if missing_drugs or missing_concepts:
                logger.info(
                    "[RelevanceFilter] Best match missing — drugs=%s concepts=%s → forcing Claude",
                    missing_drugs, missing_concepts,
                )
                match_ids = []
                score_by_id = {}
                answer_by_id = {}

    elif engine_name == "knn":
        try:
            matches2 = knn_search(search_query, top_k=top_k)
            match_ids = [m.id for m in matches2]
            score_by_id = {m.id: float(m.score) for m in matches2}
            answer_by_id = {}
        except (ModuleNotFoundError, ImportError):
            matches = tfidf_search(user_query, top_k=top_k)
            match_ids = [m.id for m in matches]
            score_by_id = {m.id: float(m.score) for m in matches}
            answer_by_id = {m.id: m.answer for m in matches}
    else:
        raise HTTPException(status_code=400, detail="engine must be 'tfidf' or 'knn'")

    # Get the best match score
    best_score = max(score_by_id.values()) if score_by_id else 0.0
    logger.info("[Search] TF-IDF best score: %.3f", best_score)
    
    source = "database"
    saved_to_db = False

    # STEP 2: Check for near-exact match (95%+ similarity)
    if best_score >= NEAR_EXACT_THRESHOLD and match_ids:
        best_id = match_ids[0]
        cached_answer = answer_by_id.get(best_id)
        if cached_answer:
            logger.info("[Search] Near-exact match (%.1f%%) — returning from database", best_score * 100)
            # Fetch full details from DB
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        select(
                            questions_table.c.id,
                            questions_table.c.question,
                            questions_table.c.category,
                            questions_table.c.tags,
                            questions_table.c.answer,
                        ).where(questions_table.c.id == best_id)
                    ).mappings().first()
                
                if row and row.get("answer"):
                    answer_text = str(row["answer"])
                    if _is_corrupted_db_answer(answer_text):
                        logger.warning("[DB] Corrupted answer in near-exact match Q#%s — bypassing to Claude", row["id"])
                    elif _is_wrong_drug_answer(user_query, answer_text):
                        logger.warning("[DB] Wrong-drug answer in near-exact match Q#%s — bypassing to Claude", row["id"])
                    else:
                        _log_search(user_query, best_id)
                        return SearchResponse(
                            query=user_query,
                            results=[
                                QuestionMatch(
                                    id=int(row["id"]),
                                    question=str(row["question"]),
                                    category=row.get("category"),
                                    tags=[str(t) for t in (row.get("tags") or [])],
                                    score=best_score,
                                    answer=answer_text,
                                    structured=_parse_structured_answer(answer_text, str(row["question"])),
                                )
                            ],
                            did_you_mean=did_you_mean,
                            source="database",
                            saved_to_db=False,
                        )
            except Exception as e:
                logger.error("[Search] Error fetching near-exact match: %s", e)

    # Check confidence threshold
    if best_score < CONFIDENCE_THRESHOLD or not match_ids:
        # Low confidence: generate live Claude answer
        logger.info("[Confidence] Best score %.3f < threshold %.3f, using Claude", best_score, CONFIDENCE_THRESHOLD)

        if _anthropic_api_key():
            try:
                live_answer, live_cits, live_intent, live_rs = _generate_ai_answer(original_query)
                source = "ai_generated"

                def _enrich(sa: "StructuredAnswer") -> "StructuredAnswer":
                    """Attach engine-v2 metadata to a parsed StructuredAnswer."""
                    sa.citations = live_cits
                    sa.intent = live_intent
                    sa.retrieval_status = live_rs
                    return sa

                # Self-learning: check if this is a valid pharmacy question
                if _is_valid_pharmacy_question(original_query):
                    category = _get_best_category(original_query)
                    new_id = _save_question_to_db(original_query, live_answer, category)
                    if new_id:
                        saved_to_db = True
                        # Return the newly saved question as a result
                        results = [
                            QuestionMatch(
                                id=new_id,
                                question=original_query,
                                category=category,
                                tags=[category.lower()],
                                score=1.0,
                                answer=live_answer,
                                structured=_enrich(_parse_structured_answer(live_answer, original_query)),
                            )
                        ]
                        _log_search(user_query, new_id)
                        return SearchResponse(
                            query=original_query,
                            results=results,
                            did_you_mean=did_you_mean,
                            source=source,
                            saved_to_db=saved_to_db,
                        )

                # Return AI-generated answer without saving
                results = [
                    QuestionMatch(
                        id=0,  # No database ID
                        question=original_query,
                        category="General",
                        tags=[],
                        score=best_score,
                        answer=live_answer,
                        structured=_enrich(_parse_structured_answer(live_answer, original_query)),
                    )
                ]
                _log_search(user_query, None)
                return SearchResponse(
                    query=original_query,
                    results=results,
                    did_you_mean=did_you_mean,
                    source=source,
                    saved_to_db=saved_to_db,
                )
            except Exception as exc:
                logger.error("[Claude] Failed to generate live answer: %s", exc, exc_info=True)
                # Fall through to database results

    # High confidence: return database results
    if not match_ids:
        return SearchResponse(
            query=original_query,
            results=[],
            did_you_mean=did_you_mean,
            source=source,
            saved_to_db=saved_to_db,
        )

    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    select(
                        questions_table.c.id,
                        questions_table.c.question,
                        questions_table.c.category,
                        questions_table.c.tags,
                        questions_table.c.answer,
                    ).where(questions_table.c.id.in_(match_ids))
                )
                .mappings()
                .all()
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database error while searching. Did you run `python data/seed_db.py`? ({e})",
        )

    row_by_id = {int(r["id"]): dict(r) for r in rows}

    results: list[QuestionMatch] = []
    generated_answers: dict[int, str] = {}

    for qid in match_ids:
        r = row_by_id.get(int(qid))
        if not r:
            continue
        tags = r.get("tags") or []

        existing_answer = r.get("answer")
        answer_text: str | None = str(existing_answer).strip() if existing_answer else None

        # Discard corrupted DB answers that contain leaked internal prompt text.
        # These were stored during earlier system versions before the format was
        # stabilised. Treat them as missing so Claude regenerates a clean answer.
        if answer_text and _is_corrupted_db_answer(answer_text):
            logger.warning("[DB] Corrupted answer detected for Q#%s — forcing regeneration", r["id"])
            answer_text = None
        elif answer_text and _is_wrong_drug_answer(user_query, answer_text):
            logger.warning("[DB] Wrong-drug answer detected for Q#%s — forcing regeneration", r["id"])
            answer_text = None

        # Generate answer for database questions that don't have one yet
        if not answer_text and _anthropic_api_key():
            try:
                answer_text, _, _, _ = _generate_ai_answer(str(r["question"]))
                generated_answers[int(r["id"])] = answer_text
            except Exception as exc:
                logger.error("[Claude] Failed to generate answer for Q#%s: %s", r["id"], exc, exc_info=True)
                answer_text = None

        results.append(
            QuestionMatch(
                id=int(r["id"]),
                question=str(r["question"]),
                category=r.get("category"),
                tags=[str(t) for t in tags],
                score=score_by_id.get(int(qid)),
                answer=answer_text,
                structured=_parse_structured_answer(answer_text, user_query) if answer_text else None,
            )
        )

    # Persist any newly generated answers (best-effort)
    if generated_answers:
        try:
            with engine.begin() as conn:
                for qid, ans in generated_answers.items():
                    conn.execute(
                        questions_table.update()
                        .where(questions_table.c.id == int(qid))
                        .values(answer=ans)
                    )
            logger.info("[DB] Cached %d new Claude answers to questions table", len(generated_answers))
        except Exception as exc:
            logger.error("[DB] Failed to cache Claude answers: %s", exc, exc_info=True)

    _log_search(user_query, match_ids[0] if match_ids else None)

    return SearchResponse(
        query=original_query,
        results=results,
        did_you_mean=did_you_mean,
        source=source,
        saved_to_db=saved_to_db,
    )


@app.post("/search/stream")
def search_stream(req: SearchRequest) -> StreamingResponse:
    """
    SSE streaming variant of /search.

    Sends Server-Sent Events so the browser connection stays alive during
    Railway cold-starts and long Claude API calls — bypassing the 30-second
    proxy read-timeout that affects buffered responses.

    Event types:
      data: {"type": "status",  "message": "..."}          — progress ping
      data: {"type": "done",    "source": "...", "result": {...}}  — final answer
      data: {"type": "error",   "message": "..."}           — failure
    """

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def generate():
        user_query = req.query.strip()
        if not user_query:
            yield _sse({"type": "error", "message": "Query cannot be empty."})
            return

        yield _sse({"type": "status", "message": "Searching database..."})

        # ── Exact match (instant) ───────────────────────────────────────────
        try:
            from services.drug_resolver import resolve_query_drugs
            resolved = resolve_query_drugs(user_query)
            if resolved:
                rewritten = user_query
                for rd in resolved:
                    inp, gen, cor = rd.get("input", ""), rd.get("generic", ""), rd.get("corrected", "")
                    if cor and cor.lower() != inp.lower() and cor.lower() in rewritten.lower():
                        rewritten = re.sub(re.escape(cor), gen, rewritten, flags=re.IGNORECASE)
                if rewritten != user_query:
                    user_query = rewritten
        except Exception:
            pass

        exact = find_exact_match(user_query)
        if (
            exact and exact.answer
            and not _is_corrupted_db_answer(exact.answer)
            and not _is_wrong_drug_answer(user_query, exact.answer)
        ):
            structured = _parse_structured_answer(exact.answer, exact.question)
            yield _sse({
                "type": "done",
                "source": "database",
                "result": {
                    "id": exact.id,
                    "question": exact.question,
                    "category": exact.category,
                    "tags": exact.tags or [],
                    "score": 1.0,
                    "answer": exact.answer,
                    "structured": structured,
                },
            })
            return

        # ── TF-IDF search ───────────────────────────────────────────────────
        original_query, cleaned_query = normalize_query(user_query)
        search_query = cleaned_query if cleaned_query else user_query
        did_you_mean = spell_check_query(search_query)

        drug_names = _extract_drug_names(search_query)
        query_intent = _classify_query_intent(search_query)
        enhanced_query = _build_intent_query(search_query, query_intent, drug_names)

        matches = tfidf_search(search_query, top_k=3)
        if enhanced_query != search_query:
            extra = tfidf_search(enhanced_query, top_k=3)
            seen = {m[0] for m in matches}
            matches += [m for m in extra if m[0] not in seen]

        if matches:
            best_id, best_score = matches[0]
            if best_score >= 0.35:
                try:
                    with engine.connect() as conn:
                        row = conn.execute(
                            select(questions_table).where(questions_table.c.id == int(best_id))
                        ).mappings().first()
                    if row:
                        ans = str(row.get("answer") or "").strip() or None
                        if ans and not _is_corrupted_db_answer(ans) and not _is_wrong_drug_answer(user_query, ans):
                            structured = _parse_structured_answer(ans, user_query)
                            yield _sse({
                                "type": "done",
                                "source": "database",
                                "result": {
                                    "id": int(row["id"]),
                                    "question": str(row["question"]),
                                    "category": row.get("category"),
                                    "tags": list(row.get("tags") or []),
                                    "score": best_score,
                                    "answer": ans,
                                    "structured": structured,
                                },
                            })
                            return
                except Exception as exc:
                    logger.warning("[Stream] DB lookup failed: %s", exc)

        # ── AI generation ───────────────────────────────────────────────────
        if not _anthropic_api_key():
            yield _sse({"type": "error", "message": "AI unavailable — no API key configured."})
            return

        yield _sse({"type": "status", "message": "Generating answer with AI..."})

        try:
            answer_text, citations, intent_str, rs = _generate_ai_answer(original_query)
            structured = _parse_structured_answer(answer_text, original_query)
            yield _sse({
                "type": "done",
                "source": "ai_generated",
                "result": {
                    "id": None,
                    "question": original_query,
                    "category": None,
                    "tags": [],
                    "score": 1.0,
                    "answer": answer_text,
                    "structured": structured,
                },
            })
        except Exception as exc:
            logger.error("[Stream] AI generation failed: %s", exc, exc_info=True)
            yield _sse({"type": "error", "message": "Could not generate an answer. Please try again."})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering on Railway
        },
    )


def _log_search(query: str, matched_question_id: int | None) -> None:
    """Log a search to the search_logs table (best-effort)."""
    try:
        with engine.begin() as conn:
            conn.execute(
                search_logs_table.insert(),
                {
                    "query": query,
                    "matched_question_id": matched_question_id,
                    "clicked": False,
                    "session_id": None,
                    "searched_at": _utc_now(),
                },
            )
    except Exception:
        pass


# ---------- Drug Image Endpoint (BUG 3 Fix - Category-Based SVG Pills) ----------

# Drug category classification for visual icons
HIGH_RISK_DRUGS = {"warfarin", "methotrexate", "lithium", "digoxin", "insulin", "heparin", "phenytoin", "theophylline"}
CONTROLLED_DRUGS = {"oxycodone", "hydrocodone", "adderall", "xanax", "valium", "morphine", "codeine", "fentanyl", "tramadol", "alprazolam", "diazepam", "amphetamine", "methylphenidate", "ritalin"}
ANTIBIOTIC_DRUGS = {"amoxicillin", "azithromycin", "ciprofloxacin", "doxycycline", "penicillin", "metronidazole", "clindamycin", "cephalexin", "levofloxacin", "sulfamethoxazole"}
PRESCRIPTION_DRUGS = {"metformin", "lisinopril", "atorvastatin", "metoprolol", "sertraline", "fluoxetine", "escitalopram", "omeprazole", "losartan", "amlodipine", "levothyroxine", "gabapentin", "prednisone", "sildenafil", "tadalafil"}
# OTC = everything else (ibuprofen, acetaminophen, aspirin, etc.)

# Category colors for pill SVGs
CATEGORY_COLORS = {
    "OTC": {"fill": "#52B788", "stroke": "#2D6A4F"},  # Green
    "PRESCRIPTION": {"fill": "#3B82F6", "stroke": "#1E40AF"},  # Blue
    "HIGH_RISK": {"fill": "#EF4444", "stroke": "#991B1B"},  # Red
    "ANTIBIOTIC": {"fill": "#F97316", "stroke": "#C2410C"},  # Orange
    "CONTROLLED": {"fill": "#8B5CF6", "stroke": "#5B21B6"},  # Purple
}

def _get_pill_svg(category: str) -> str:
    """Generate a clean pill capsule SVG with category-appropriate colors."""
    colors = CATEGORY_COLORS.get(category, CATEGORY_COLORS["OTC"])
    fill = colors["fill"]
    stroke = colors["stroke"]
    
    # Clean pill capsule SVG - horizontal orientation
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60" width="60" height="60">
  <!-- Pill shadow -->
  <ellipse cx="30" cy="52" rx="20" ry="4" fill="rgba(0,0,0,0.15)"/>
  <!-- Pill body - left half (colored) -->
  <path d="M10 30 C10 19 18 12 30 12 L30 48 C18 48 10 41 10 30" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
  <!-- Pill body - right half (white) -->
  <path d="M30 12 C42 12 50 19 50 30 C50 41 42 48 30 48 L30 12" fill="#FFFFFF" stroke="{stroke}" stroke-width="1.5"/>
  <!-- Center divider line -->
  <line x1="30" y1="12" x2="30" y2="48" stroke="{stroke}" stroke-width="1"/>
  <!-- Shine highlight on colored side -->
  <ellipse cx="20" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.35)"/>
  <!-- Shine highlight on white side -->
  <ellipse cx="40" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.5)"/>
</svg>'''


def _get_drug_category(drug_name: str) -> str:
    """
    Classify a drug into a category based on keyword matching.
    Returns: OTC, PRESCRIPTION, HIGH_RISK, ANTIBIOTIC, or CONTROLLED
    """
    if not drug_name:
        return "OTC"
    
    drug_lower = drug_name.lower().strip()
    
    # Check each category (order matters - check high risk first)
    if drug_lower in HIGH_RISK_DRUGS:
        return "HIGH_RISK"
    if drug_lower in CONTROLLED_DRUGS:
        return "CONTROLLED"
    if drug_lower in ANTIBIOTIC_DRUGS:
        return "ANTIBIOTIC"
    if drug_lower in PRESCRIPTION_DRUGS:
        return "PRESCRIPTION"
    
    # Default to OTC for common OTC drugs and unknowns
    return "OTC"


class DrugImageResponse(BaseModel):
    drug_name: str
    category: str = "OTC"  # OTC, PRESCRIPTION, HIGH_RISK, ANTIBIOTIC, CONTROLLED
    category_label: str = "Over-the-Counter"
    svg_data: str


@app.get("/drug-image", response_model=DrugImageResponse)
def get_drug_image(name: str) -> DrugImageResponse:
    """
    Get drug category and SVG pill icon for a given drug name.
    
    BUG 3 FIX: Returns category-based colored pill SVG instead of broken image URLs.
    Categories:
    - OTC (green): ibuprofen, acetaminophen, aspirin, etc.
    - PRESCRIPTION (blue): metformin, lisinopril, atorvastatin, etc.
    - HIGH_RISK (red): warfarin, methotrexate, lithium, etc.
    - ANTIBIOTIC (orange): amoxicillin, azithromycin, ciprofloxacin, etc.
    - CONTROLLED (purple): oxycodone, xanax, adderall, etc.
    """
    drug_name = name.strip().lower() if name else ""
    
    # Convert brand to generic if known
    generic_name = BRAND_TO_GENERIC.get(drug_name, drug_name) if drug_name else ""
    
    # Get category
    category = _get_drug_category(generic_name)
    
    # Category labels for display
    category_labels = {
        "OTC": "Over-the-Counter",
        "PRESCRIPTION": "Prescription",
        "HIGH_RISK": "High-Risk",
        "ANTIBIOTIC": "Antibiotic",
        "CONTROLLED": "Controlled",
    }
    
    return DrugImageResponse(
        drug_name=generic_name or "unknown",
        category=category,
        category_label=category_labels.get(category, "Over-the-Counter"),
        svg_data=_get_pill_svg(category),
    )


# ---------- Pill Image endpoint ----------

class PillImageResponse(BaseModel):
    drug_name: str
    image_url: Optional[str] = None
    source: str = "fallback"


@app.get("/pill-image", response_model=PillImageResponse)
def get_pill_image(name: str) -> PillImageResponse:
    """
    Get a real pill photo URL from NIH RxImageAccess.
    Falls back to { image_url: null, source: 'fallback' } if not found —
    the frontend should render the SVG pill icon in that case.
    """
    drug_name = name.strip().lower() if name else ""
    if not drug_name:
        raise HTTPException(status_code=400, detail="name parameter is required.")

    generic_name = BRAND_TO_GENERIC.get(drug_name, drug_name)
    image_url = _fetch_pill_image(generic_name)

    return PillImageResponse(
        drug_name=generic_name,
        image_url=image_url,
        source="rximage" if image_url else "fallback",
    )


# ---------- Drug Index endpoint ----------

# In-memory cache so repeated /drug-index calls don't hammer external APIs
_drug_index_cache: dict[str, list] = {}

# Master drug list for the index — all known drugs across all categories
_ALL_KNOWN_DRUGS: list[str] = sorted(set(
    list(BRAND_TO_GENERIC.values()) + [
        "acetaminophen", "ibuprofen", "aspirin", "naproxen", "diphenhydramine",
        "loratadine", "cetirizine", "fexofenadine", "omeprazole", "famotidine",
        "esomeprazole", "metformin", "lisinopril", "atorvastatin", "metoprolol",
        "sertraline", "fluoxetine", "escitalopram", "losartan", "amlodipine",
        "levothyroxine", "gabapentin", "prednisone", "sildenafil", "tadalafil",
        "warfarin", "methotrexate", "lithium", "digoxin", "insulin", "heparin",
        "phenytoin", "theophylline", "oxycodone", "hydrocodone", "alprazolam",
        "diazepam", "morphine", "codeine", "fentanyl", "tramadol", "amphetamine",
        "methylphenidate", "amoxicillin", "azithromycin", "ciprofloxacin",
        "doxycycline", "penicillin", "metronidazole", "clindamycin", "cephalexin",
        "levofloxacin", "sulfamethoxazole", "rosuvastatin", "zolpidem",
    ]
))

_CATEGORY_LABELS = {
    "OTC": "Over-the-Counter",
    "PRESCRIPTION": "Prescription",
    "HIGH_RISK": "High-Risk",
    "ANTIBIOTIC": "Antibiotic",
    "CONTROLLED": "Controlled",
}


class DrugIndexEntry(BaseModel):
    drug_name: str
    category: str
    category_label: str
    image_url: Optional[str] = None
    svg_data: str


class DrugIndexResponse(BaseModel):
    letter: Optional[str] = None
    total: int
    drugs: list[DrugIndexEntry]


@app.get("/drug-index", response_model=DrugIndexResponse)
def get_drug_index(letter: Optional[str] = None) -> DrugIndexResponse:
    """
    Returns all known drugs, optionally filtered by first letter.
    Results are cached in memory after first call per letter.
    Each entry includes: drug_name, category, category_label, image_url, svg_data.
    """
    cache_key = (letter or "ALL").upper()

    if cache_key in _drug_index_cache:
        cached = _drug_index_cache[cache_key]
        return DrugIndexResponse(letter=letter, total=len(cached), drugs=cached)

    filtered = _ALL_KNOWN_DRUGS
    if letter:
        letter_upper = letter.strip().upper()
        filtered = [d for d in _ALL_KNOWN_DRUGS if d.upper().startswith(letter_upper)]

    entries: list[DrugIndexEntry] = []
    for drug in filtered:
        category = _get_drug_category(drug)
        image_url = _fetch_pill_image(drug)
        entries.append(DrugIndexEntry(
            drug_name=drug,
            category=category,
            category_label=_CATEGORY_LABELS.get(category, "Over-the-Counter"),
            image_url=image_url,
            svg_data=_get_pill_svg(category),
        ))

    _drug_index_cache[cache_key] = entries
    logger.info("[DrugIndex] Built index for letter=%s — %d drugs", cache_key, len(entries))

    return DrugIndexResponse(letter=letter, total=len(entries), drugs=entries)


@app.post("/log", response_model=LogResponse)
def log_search(req: LogRequest) -> LogResponse:
    """
    Logs a search event to `search_logs`.

    Your frontend can call this after /search (or whenever the user clicks a result).
    """
    q = req.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    row = {
        "query": q,
        "matched_question_id": req.matched_question_id,
        "clicked": bool(req.clicked),
        "session_id": req.session_id,
        "searched_at": _utc_now(),
    }

    try:
        with engine.begin() as conn:
            result = conn.execute(search_logs_table.insert().returning(search_logs_table.c.id), row)
            log_id = int(result.scalar_one())
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Database error while logging. ({e})")

    return LogResponse(ok=True, log_id=log_id)


# ---------- Admin: label cache stats & manual refresh ----------

@app.get("/admin/cache-stats")
def admin_cache_stats() -> dict:
    """
    Returns label_updater cache statistics.
    Useful for monitoring label freshness in production.
    """
    return label_updater.cache_stats()


@app.post("/admin/refresh-label")
def admin_refresh_label(drug: str) -> dict:
    """
    Force-refresh a single drug label from OpenFDA, bypassing the TTL cache.
    Query param: ?drug=metformin
    """
    if not drug.strip():
        raise HTTPException(status_code=400, detail="drug parameter is required")

    drug_name = drug.strip().lower()
    # Evict from cache so next fetch is fresh
    import label_updater as _lu
    if drug_name in _lu._cache:
        del _lu._cache[drug_name]

    fda_data, raw_label = _fetch_fda_label_with_raw(drug_name)
    if not fda_data:
        return {"refreshed": False, "drug": drug_name, "reason": "label not found in OpenFDA"}

    meta = label_updater.get_label_metadata(drug_name) or {}
    return {
        "refreshed": True,
        "drug": drug_name,
        "label_revision_date": meta.get("label_revision_date"),
        "date_fetched": meta.get("date_fetched"),
        "cache_expires_at": meta.get("cache_expires_at"),
    }
