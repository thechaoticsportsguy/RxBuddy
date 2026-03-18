from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import requests as http_requests
from dotenv import load_dotenv
from spellchecker import SpellChecker

logger = logging.getLogger("rxbuddy")
logging.basicConfig(level=logging.INFO)
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, MetaData, Table, Text, create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, VARCHAR


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
    """Extract ALL drug names mentioned in a question (not just the first)."""
    q_lower = question.lower()
    found: list[str] = []

    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower and generic not in found:
            found.append(generic)

    all_drugs = [
        "acetaminophen", "ibuprofen", "aspirin", "naproxen", "amoxicillin",
        "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
        "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
        "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
        "hydrocodone", "oxycodone", "tramadol", "warfarin", "ciprofloxacin",
    ]
    for drug in all_drugs:
        if drug in q_lower and drug not in found:
            found.append(drug)

    return found


def _classify_query_intent(question: str) -> str:
    """Rule-based intent classification. Returns intent key or 'general'."""
    q_lower = question.lower()
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_SIGNALS.items():
        scores[intent] = sum(1 for kw in keywords if kw in q_lower)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "general"


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
    Fetch FDA label data from OpenFDA API.
    Returns relevant sections or None if not found.
    """
    if not drug_name:
        return None
    
    try:
        url = f"{OPENFDA_LABEL_URL}?search=openfda.generic_name:{drug_name}&limit=1"
        logger.info("[FDA] Fetching label for '%s' from OpenFDA...", drug_name)
        
        resp = http_requests.get(
            url,
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=10,
        )
        
        if resp.status_code == 404:
            logger.info("[FDA] No label found for '%s'", drug_name)
            return None
        
        resp.raise_for_status()
        data = resp.json()
        
        results = data.get("results", [])
        if not results:
            return None
        
        label = results[0]
        
        # Extract relevant sections (each is a list of strings)
        def get_section(key: str) -> str:
            val = label.get(key, [])
            if isinstance(val, list) and val:
                return val[0][:1000]  # Limit to 1000 chars
            return ""
        
        fda_data = {
            "drug_name": drug_name,
            "warnings": get_section("warnings"),
            "dosage_and_administration": get_section("dosage_and_administration"),
            "contraindications": get_section("contraindications"),
            "drug_interactions": get_section("drug_interactions"),
            "adverse_reactions": get_section("adverse_reactions"),
            "pregnancy": get_section("pregnancy"),
            "indications_and_usage": get_section("indications_and_usage"),
        }
        
        # Check if we got any useful data
        has_data = any(v for k, v in fda_data.items() if k != "drug_name" and v)
        if not has_data:
            return None
        
        logger.info("[FDA] Successfully fetched label for '%s'", drug_name)
        return fda_data
        
    except Exception as e:
        logger.warning("[FDA] Error fetching label for '%s': %s", drug_name, e)
        return None


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
    "dosage": "dosage dose mg adults",
    "side_effects": "side effects adverse reactions symptoms",
    "pregnancy": "pregnancy safe pregnant breastfeeding",
    "storage": "storage expiration shelf life",
    "alcohol": "alcohol drink interaction",
    "overdose": "overdose maximum dose toxic",
    "safety": "safety warnings precautions",
}

# Intent → DB category name (must match PHARMACY_CATEGORIES values exactly)
_INTENT_TO_CATEGORY: dict[str, str] = {
    "interaction": "Drug Interactions",
    "dosage": "Dosage",
    "side_effects": "Side Effects",
    "pregnancy": "Pregnancy",
    "storage": "Storage",
    "alcohol": "Alcohol",
    "overdose": "Overdose",
    "safety": "Warnings",
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

    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)
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

    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)
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
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=api_key)
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
If any issue is found, use EXACTLY this structure:
VERDICT: ...
DIRECT: ...
WHY: ...
DO: item 1 | item 2
AVOID: item 1 | item 2
DOCTOR: item 1 | item 2
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


def _generate_ai_answer(question: str) -> str:
    """
    Generate a structured, specific answer using Anthropic Claude.
    
    Implements 5 hallucination guardrails:
    1. Allow "I don't know" - Claude can admit uncertainty
    2. Ground in FDA data - Fetch real FDA label data first
    3. Require citations - SOURCES field required
    4. Chain of thought - Think through drug, concern, FDA data
    5. Confidence scoring - HIGH/MEDIUM/LOW rating
    
    Returns answer in a parseable format with confidence and sources.
    """
    api_key = _anthropic_api_key()
    if not api_key:
        logger.error("[Claude] ANTHROPIC_API_KEY is NOT set in environment!")
        raise RuntimeError("ANTHROPIC_API_KEY is missing.")

    logger.info("[Claude] API key found (first 8 chars): %s...", api_key[:8] if len(api_key) > 8 else "***")

    # Ground answers in FDA + MedlinePlus data — extract all drugs, fetch label for primary
    drug_names = _extract_drug_names(question)
    drug_name = drug_names[0] if drug_names else _extract_drug_name(question)
    query_intent = _classify_query_intent(question)
    fda_data = _fetch_fda_label(drug_name) if drug_name else None
    medlineplus_data = _fetch_medlineplus(drug_name) if drug_name else None
    fda_context = _build_fda_context(fda_data, question, medlineplus_data)

    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Build context preamble for multi-drug queries
        drug_context = ""
        if len(drug_names) >= 2:
            drug_context = f"DRUGS IN THIS QUERY: {', '.join(drug_names)}\nINTENT: {query_intent}\n\n"

        system_prompt = drug_context + """You are a clinical-grade AI pharmacist powering RxBuddy.

Your PRIMARY job is NOT just answering questions — it is to:
1. Classify the query correctly
2. Assign the CORRECT SAFETY VERDICT
3. Ensure the verdict MATCHES the explanation
4. Never contradict yourself

VERDICT LOGIC (STRICT):
- AVOID → High risk, dangerous, contraindicated
- CAUTION → Moderate risk, monitoring needed, "may increase risk", "use with caution", "monitor"
- CONSULT PHARMACIST → Dosage questions, personalization needed, unclear safety
- SAFE → ONLY if truly no meaningful risk exists

HARD RULE — if your explanation contains ANY of:
"moderate interaction" / "monitor" / "may increase risk" / "use with caution"
→ VERDICT MUST BE CAUTION, never SAFE

COMMON CASES (memorize these):
- metformin + ibuprofen → CAUTION (kidney strain, lactic acidosis risk)
- Tylenol in pregnancy → CAUTION (generally safe but dose-dependent)
- ibuprofen + warfarin → AVOID (major bleeding risk)
- ibuprofen while pregnant → AVOID (especially 3rd trimester)

INTENT DETECTION — classify as one of:
interaction, multi_drug, pregnancy, dosage, side_effects, alcohol, special_population, other

DRUG EXTRACTION — extract ALL substances:
"metformin and ibuprofen" → ["metformin", "ibuprofen"]
If multiple drugs → ALWAYS run interaction logic

STRICT RULES:
- ONLY answer about drugs mentioned in the question
- NEVER introduce a drug not in the question
- NEVER hallucinate
- If unsure → return CONSULT PHARMACIST
- NEVER say SAFE if ANY risk exists
- FDA clinical data is the source of truth for interactions, contraindications, warnings, and severity
- MedlinePlus is only for plain-English explanation and side effects
- If sources conflict, always choose the more dangerous interpretation

RESPONSE FORMAT (return exactly this):

VERDICT: [SAFE / CAUTION / AVOID / CONSULT PHARMACIST]
DIRECT: one plain-English sentence that directly answers the question
WHY: 1-2 plain-English sentences explaining the risk or lack of risk
DO: action item 1 | action item 2
AVOID: thing to avoid 1 | thing to avoid 2
DOCTOR: red flag symptom 1 | red flag symptom 2
CONFIDENCE: [HIGH / MEDIUM / LOW]
SOURCES: FDA label | MedlinePlus | RxNorm

FORMAT RULES:
- Use the exact uppercase labels above
- Do not use markdown headings
- Do not return bullets outside the DO / AVOID / DOCTOR lines
- If there is a moderate interaction or monitoring need, VERDICT must be CAUTION
- If there is a serious interaction, contraindication, or major harm risk, VERDICT must be AVOID

FINAL VALIDATION (run silently before output):
1. Do drugs in answer match drugs in question?
2. Does verdict match explanation? If explanation mentions risk → verdict cannot be SAFE
3. Any contradictions?
4. Any missing risk?
If ANY fail → fix before outputting."""

        if fda_context:
            user_content = (
                f"FDA LABEL DATA (use as primary source when relevant):\n"
                f"{fda_context}\n\n"
                f"PATIENT QUESTION: {question}"
            )
        else:
            user_content = f"PATIENT QUESTION: {question}"

        logger.info("[Claude] Sending question with FDA grounding to claude-sonnet-4-20250514: %.80s...", question)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        logger.info("[Claude] Response received. Content blocks: %d", len(response.content))

        if not response.content:
            logger.error("[Claude] Response has no content blocks!")
            raise RuntimeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            logger.warning("[Claude] Empty text in response for question: %.80s", question)
            raise RuntimeError("Claude returned an empty response.")

        answer = _truncate_words(text, 400)  # Increased for structured safety format
        answer = _validate_ai_answer(question, answer)
        logger.info("[Claude] SUCCESS! Generated answer (%d words): %.200s", len(answer.split()), answer)
        print(f"[Claude] ANSWER: {answer}")  # Also print to stdout for Railway logs
        return answer

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


@app.on_event("startup")
def startup_event() -> None:
    """Initialize spell checker with RxNorm drug names on startup."""
    _init_spell_checker()


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "RxBuddy is live"}


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
    verdict: str = "CONSULT_PHARMACIST"  # SAFE, AVOID, CAUTION, CONSULT_PHARMACIST
    direct: str = ""  # Direct YES/NO answer
    do: list[str] = []  # What to do bullets
    avoid: list[str] = []  # What to avoid bullets
    doctor: list[str] = []  # See a doctor if bullets
    raw: str = ""  # Original raw answer text
    confidence: str = "MEDIUM"  # HIGH, MEDIUM, or LOW
    sources: str = ""  # FDA label, DailyMed, etc.
    interaction_summary: dict[str, list[str]] = Field(
        default_factory=lambda: {"avoid_pairs": [], "caution_pairs": []}
    )


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

        label_boundary = r"(?=^\s*(?:VERDICT|ANSWER|DIRECT|WHY|DO|AVOID|DOCTOR|WARNING|GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF|SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?|CONFIDENCE|SOURCES)\s*[:\-]|\Z)"

        verdict_raw = _extract_field([
            rf"^\s*(?:VERDICT|ANSWER)\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        result.verdict = _normalize_verdict(verdict_raw) or _extract_verdict(text, question)

        direct_raw = _extract_field([
            rf"^\s*DIRECT\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        why_raw = _extract_field([
            rf"^\s*WHY\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*REASON\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        do_raw = _extract_field([
            rf"^\s*DO\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*WHAT\s+YOU\s+SHOULD\s+DO\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        avoid_raw = _extract_field([
            rf"^\s*AVOID\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*WHAT\s+TO\s+AVOID\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        doctor_raw = _extract_field([
            rf"^\s*DOCTOR\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*WARNING\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        confidence_raw = _extract_field([
            rf"^\s*CONFIDENCE\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        sources_raw = _extract_field([
            rf"^\s*SOURCES?\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])

        result.direct = direct_raw or why_raw or ""
        result.do = _split_items(do_raw)
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

        if not result.direct:
            sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " ").strip())
            if sentences and sentences[0]:
                result.direct = sentences[0].strip()

        if result.direct and not re.search(r"[.!?]$", result.direct):
            result.direct += "."

        result.interaction_summary = build_interaction_summary(question)
        result.verdict = validate_and_correct_verdict(
            text,
            result.verdict,
            question,
            interaction_summary=result.interaction_summary,
        )

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

    if _contains_any(q_lower, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if _contains_any(q_lower, SIDE_EFFECT_TERMS):
        return "CAUTION"

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

    if pairwise_verdict == "SAFE":
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

    if _contains_any(combined_text, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if summary.get("avoid_pairs"):
        return "AVOID"
    if _contains_any(combined_text, AVOID_PHRASES):
        return "AVOID"
    if summary.get("caution_pairs"):
        return "CAUTION"
    if _contains_any(combined_text, CAUTION_PHRASES):
        return "CAUTION"
    if _contains_any(combined_text, INFORMATIONAL_TERMS):
        return "CAUTION"
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
        a = _generate_ai_answer(q)
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

    logger.info("[Search] Checking exact match for: %.80s", user_query)

    # STEP 1: Check for exact match FIRST (before any processing)
    exact_match = find_exact_match(user_query)
    if exact_match and exact_match.answer:
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

        # Hard rule: multi-drug interaction query must find a question containing ALL drugs.
        # If the best match is missing any drug → force Claude (wipe match_ids).
        if query_intent == "interaction" and len(drug_names) >= 2 and matches:
            best_q_lower = (matches[0].question or "").lower()
            if not all(d in best_q_lower for d in drug_names):
                logger.info(
                    "[IntentFilter] Interaction query %s — best match missing drugs, forcing Claude",
                    drug_names,
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
                                answer=str(row["answer"]),
                                structured=_parse_structured_answer(str(row["answer"]), str(row["question"])),
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
                live_answer = _generate_ai_answer(original_query)
                source = "ai_generated"

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
                                structured=_parse_structured_answer(live_answer, original_query),
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
                        structured=_parse_structured_answer(live_answer, original_query),
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

        # Generate answer for database questions that don't have one yet
        if not answer_text and _anthropic_api_key():
            try:
                answer_text = _generate_ai_answer(str(r["question"]))
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
