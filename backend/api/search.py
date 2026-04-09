"""All API routes — search, chat, drug-image, admin, etc."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time as _time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import insert, select, text

from core.config import settings
from core.db import (
    async_engine,
    async_session_factory,
    drug_chat_cache,
    questions_table,
    search_logs_table,
    sync_engine,
)
from domain.verdicts import (
    is_corrupted_db_answer,
    is_wrong_drug_answer,
    lookup_deterministic,
    parse_structured_answer,
)
from services.fda_client import (
    BRAND_TO_GENERIC,
    build_enriched_context,
    build_fda_context,
    build_multi_drug_context,
    extract_drug_name,
    extract_drug_names,
    fetch_all_external_sources,
    fetch_all_labels,
    fetch_fda_label_with_raw,
    fetch_medlineplus,
    fetch_pill_image,
    fetch_pubmed_studies,
)

logger = logging.getLogger("rxbuddy.api")

# ── Rate limiter (exported for main.py to attach to app) ────────────────────
limiter = Limiter(key_func=get_remote_address)

router = APIRouter()


# ── Utility ──────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _classify_query_intent(question: str) -> str:
    """7-type intent classifier — delegates to answer_engine.classify_intent()."""
    drugs = extract_drug_names(question)
    from answer_engine import classify_intent

    intent = classify_intent(question, drug_count=len(drugs))
    return intent.value


# ── Intent query boost ───────────────────────────────────────────────────────

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

PHARMACY_CATEGORIES = [
    "Drug Interactions", "Dosage", "Side Effects", "Warnings",
    "Contraindications", "Pregnancy", "Storage", "Children",
    "Special Populations", "Overdose", "Adverse Reactions", "General",
    "Patient Counseling", "Patient Information", "Alcohol", "Food Interactions",
]


def _build_intent_query(question: str, intent: str, drugs: list[str]) -> str:
    boost = _INTENT_QUERY_BOOST.get(intent, "")
    return f"{question} {boost}".strip() if boost else question


def _rerank_by_intent(matches: list, drugs: list[str], intent: str) -> list:
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


# ── Required concept words ───────────────────────────────────────────────────
_REQUIRED_CONCEPT_WORDS: frozenset[str] = frozenset({
    "alcohol", "alcoholic", "drinking", "drink", "beer", "wine", "liquor",
    "pregnant", "pregnancy", "breastfeeding", "nursing", "lactation",
    "liver", "kidney", "renal", "hepatic",
    "overdose", "overdosed",
    "child", "children", "pediatric", "infant", "baby",
    "elderly",
})


# ── Normalisation ────────────────────────────────────────────────────────────

SPELLING_FIXES = {
    "tynenol": "tylenol", "ibrofen": "ibuprofen", "ibuprofin": "ibuprofen",
    "amoxicilin": "amoxicillin", "motrin": "ibuprofen", "advil": "ibuprofen",
    "aleve": "naproxen", "nyquil": "dextromethorphan",
}

SLANG_MAP = {
    "drunk": "alcohol", "wasted": "alcohol", "booze": "alcohol",
    "drinking": "alcohol", "cooked": "dangerous reaction", "meds": "medication",
    "pills": "medication", "xanny": "xanax", "addy": "adderall",
    "molly": "MDMA", "high": "intoxicated", "stoned": "cannabis",
    "head is killing me": "headache", "stomach is killing me": "stomach pain",
    "throwing up": "vomiting", "threw up": "vomiting", "feel sick": "nausea",
    "heart racing": "palpitations", "can't sleep": "insomnia",
    "knocked out": "sedated",
}

FILLER_WORDS = frozenset({"like", "um", "basically", "literally", "yo", "bro", "hey"})


def normalize_query(query: str) -> tuple[str, str]:
    original = (query or "").strip()
    if not original:
        return original, original
    q = original.lower()
    for slang, medical in sorted(SLANG_MAP.items(), key=lambda x: -len(x[0])):
        q = re.sub(r"\b" + re.escape(slang) + r"\b", medical, q, flags=re.IGNORECASE)
    for misspelling, correct in SPELLING_FIXES.items():
        q = re.sub(r"\b" + re.escape(misspelling) + r"\b", correct, q, flags=re.IGNORECASE)
    words = [w for w in q.split() if w.lower() not in FILLER_WORDS]
    cleaned = " ".join(words).strip()
    return original, cleaned if cleaned else original


# ── Spell check (initialised at startup) ─────────────────────────────────────
_medical_terms: set[str] = set()
_spell_checker = None


def init_spell_checker(terms: set[str]) -> None:
    global _medical_terms, _spell_checker
    from spellchecker import SpellChecker

    _medical_terms = terms
    _spell_checker = SpellChecker()
    if _medical_terms:
        _spell_checker.word_frequency.load_words(_medical_terms)
    extra = {
        "tylenol", "ibuprofen", "acetaminophen", "naproxen", "aspirin",
        "amoxicillin", "metformin", "lisinopril", "omeprazole", "gabapentin",
        "medication", "prescription", "dosage", "antibiotic", "painkiller",
        "allergic", "interaction", "contraindication", "pharmacy", "pharmacist",
    }
    _spell_checker.word_frequency.load_words(extra)
    logger.info("[SpellChecker] Initialized with %d medical + %d extra terms", len(_medical_terms), len(extra))


def spell_check_query(query: str) -> str | None:
    if _spell_checker is None:
        return None
    words = query.lower().split()
    if not words:
        return None
    corrected = []
    changed = False
    for word in words:
        clean = re.sub(r"[^\w]", "", word)
        if len(clean) < 3:
            corrected.append(word)
            continue
        if clean in _medical_terms or clean in _spell_checker:
            corrected.append(word)
            continue
        correction = _spell_checker.correction(clean)
        if correction and correction != clean:
            corrected.append(word.replace(clean, correction))
            changed = True
        else:
            corrected.append(word)
    return " ".join(corrected) if changed else None


# ── Non-drug query detection ─────────────────────────────────────────────────

_ILLEGAL_DRUGS = frozenset({
    "cocaine", "heroin", "meth", "methamphetamine", "crack", "ecstasy",
    "mdma", "lsd", "acid", "pcp", "angel dust", "ketamine", "shrooms",
    "psilocybin", "magic mushrooms", "fentanyl street", "molly",
    "bath salts", "flakka", "spice", "k2", "krokodil", "ghb",
    "crystal meth", "speedball", "dmt", "ayahuasca", "mescaline",
    "peyote", "salvia", "whippets", "poppers", "lean", "purple drank",
    "marijuana", "weed", "cannabis", "thc", "delta-8", "delta 8",
})

_MEDICAL_TERMS = frozenset({
    "side effect", "side effects", "dosage", "dose", "drug", "medication",
    "medicine", "prescription", "interact", "interaction", "allergy",
    "allergic", "pregnant", "pregnancy", "breastfeed", "overdose",
    "withdraw", "withdrawal", "tablet", "capsule", "pill", "mg",
    "milligram", "pharmacy", "pharmacist", "doctor", "otc",
    "over the counter", "rx", "generic", "brand", "adverse",
    "contraindic", "indication", "warning", "precaution",
    "mechanism", "half-life", "half life",
})

_REJECTION_MESSAGES = [
    "Hmm, that's not in our formulary! RxBuddy only answers questions about real medications. Try searching a drug like 'lisinopril side effects'!",
    "That one isn't in any pharmacy we know of! Try asking about a real medication like 'metformin dosage' or 'ibuprofen side effects'.",
    "RxBuddy is great at drugs (the legal kind). Try searching for a medication like 'amoxicillin' or 'atorvastatin'!",
    "We checked every shelf in the pharmacy... nothing found! Try a real drug name like 'omeprazole' or 'gabapentin side effects'.",
]

_SAMHSA_MESSAGE = (
    "RxBuddy only covers FDA-approved medications. "
    "If you or someone you know is struggling with substance use, "
    "please contact the SAMHSA National Helpline: 1-800-662-4357 (free, confidential, 24/7)."
)


def _check_non_drug_query(query: str) -> dict | None:
    """
    Returns a rejection dict if the query is clearly not about a real drug.

    FIX: Only reject short queries (<=2 words) that have no drug AND no medical term.
    This allows "ibuprofen dosage" through even though it's only 2 words.
    """
    q_lower = query.strip().lower()
    if not q_lower or len(q_lower) < 2:
        return None

    # Check for illegal/street drugs first
    for term in _ILLEGAL_DRUGS:
        if term in q_lower:
            logger.info("[NonDrugFilter] Illegal drug detected: '%s'", term)
            return {
                "intent": "non_drug_query",
                "sub_type": "illegal_drug",
                "message": _SAMHSA_MESSAGE,
                "detected_term": term,
            }

    # If the query contains a medical term, let it through
    for term in _MEDICAL_TERMS:
        if term in q_lower:
            return None

    # If query matches a known drug from CSV or brand map, let it through
    for brand in BRAND_TO_GENERIC:
        if brand in q_lower:
            return None
    try:
        from data.drug_csv_loader import load_drug_lookup

        for drug_name in load_drug_lookup():
            if drug_name in q_lower:
                return None
    except Exception:
        pass

    try:
        from pipeline.side_effects_store import _DRUG_CLASS_FALLBACKS, _DRUG_ALIASES

        for name in list(_DRUG_CLASS_FALLBACKS.keys()) + list(_DRUG_ALIASES.keys()):
            if name in q_lower:
                return None
    except Exception:
        pass

    # FIX: Only reject if <=2 words AND no drug/medical term was found above
    words = q_lower.split()
    if len(words) <= 2:
        idx = int(hashlib.md5(q_lower.encode()).hexdigest(), 16) % len(_REJECTION_MESSAGES)
        logger.info("[NonDrugFilter] Non-drug query rejected: '%s'", q_lower)
        return {
            "intent": "non_drug_query",
            "sub_type": "not_a_drug",
            "message": _REJECTION_MESSAGES[idx],
        }

    return None


# ── Parse rate limit ─────────────────────────────────────────────────────────
_PARSE_RATE_STORE: dict[str, list[float]] = {}


def _check_parse_rate_limit(drug_name: str, max_per_hour: int = 2) -> None:
    key = drug_name.strip().lower()
    now = _time.time()
    timestamps = [t for t in _PARSE_RATE_STORE.get(key, []) if now - t < 3600.0]
    if len(timestamps) >= max_per_hour:
        retry_after = int(3600.0 - (now - timestamps[0]))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {max_per_hour} parses per drug per hour. Retry in {retry_after}s.",
        )
    timestamps.append(now)
    _PARSE_RATE_STORE[key] = timestamps


# ── Request / Response models ────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    engine: str = Field("tfidf")
    top_k: int = Field(5, ge=1, le=10)


class StructuredAnswer(BaseModel):
    verdict: str = "CONSULT_PHARMACIST"
    answer: str = ""
    short_answer: str = ""
    warning: str = ""
    details: list[str] = []
    action: list[str] = []
    article: str = ""
    direct: str = ""
    do: list[str] = []
    avoid: list[str] = []
    doctor: list[str] = []
    raw: str = ""
    confidence: str = "MEDIUM"
    sources: str = ""
    interaction_summary: dict[str, list[str]] = Field(
        default_factory=lambda: {"avoid_pairs": [], "caution_pairs": []}
    )
    citations: list[dict] = Field(default_factory=list)
    intent: str = "general"
    retrieval_status: str = "LABEL_NOT_FOUND"
    drug: str = ""
    common_side_effects: str = ""
    mechanism: str = ""
    serious_side_effects: list[str] = Field(default_factory=list)
    warning_signs: list[str] = Field(default_factory=list)
    pubmed_studies: list[dict] = Field(default_factory=list)
    generic_name: str = ""
    brand_names: list[str] = Field(default_factory=list)
    side_effects_data: dict = Field(default_factory=dict)
    boxed_warnings: list[str] = Field(default_factory=list)
    mechanism_of_action: dict = Field(default_factory=dict)
    structured_sources: list[dict] = Field(default_factory=list)


class QuestionMatch(BaseModel):
    id: int
    question: str
    category: Optional[str] = None
    tags: list[str] = []
    score: Optional[float] = None
    answer: Optional[str] = None
    structured: Optional[StructuredAnswer] = None


class SearchResponse(BaseModel):
    query: str
    results: list[QuestionMatch]
    did_you_mean: Optional[str] = None
    source: str = "database"
    saved_to_db: bool = False


class LogRequest(BaseModel):
    query: str = Field(..., min_length=1)
    matched_question_id: Optional[int] = None
    clicked: bool = False
    session_id: Optional[str] = None


class LogResponse(BaseModel):
    ok: bool
    log_id: int


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AnswerResponse(BaseModel):
    question: str
    answer: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    drug_name: str
    message: str
    conversation_history: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str


class DrugImageResponse(BaseModel):
    drug_name: str
    category: str = "OTC"
    category_label: str = "Over-the-Counter"
    svg_data: str


class PillImageResponse(BaseModel):
    drug_name: str
    image_url: Optional[str] = None
    source: str = "fallback"


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


# ── Drug categories for pill SVGs ────────────────────────────────────────────

HIGH_RISK_DRUGS = {"warfarin", "methotrexate", "lithium", "digoxin", "insulin", "heparin", "phenytoin", "theophylline"}
CONTROLLED_DRUGS = {"oxycodone", "hydrocodone", "adderall", "xanax", "valium", "morphine", "codeine", "fentanyl", "tramadol", "alprazolam", "diazepam", "amphetamine", "methylphenidate", "ritalin"}
ANTIBIOTIC_DRUGS = {"amoxicillin", "azithromycin", "ciprofloxacin", "doxycycline", "penicillin", "metronidazole", "clindamycin", "cephalexin", "levofloxacin", "sulfamethoxazole"}
PRESCRIPTION_DRUGS = {"metformin", "lisinopril", "atorvastatin", "metoprolol", "sertraline", "fluoxetine", "escitalopram", "omeprazole", "losartan", "amlodipine", "levothyroxine", "gabapentin", "prednisone", "sildenafil", "tadalafil"}

CATEGORY_COLORS = {
    "OTC": {"fill": "#52B788", "stroke": "#2D6A4F"},
    "PRESCRIPTION": {"fill": "#3B82F6", "stroke": "#1E40AF"},
    "HIGH_RISK": {"fill": "#EF4444", "stroke": "#991B1B"},
    "ANTIBIOTIC": {"fill": "#F97316", "stroke": "#C2410C"},
    "CONTROLLED": {"fill": "#8B5CF6", "stroke": "#5B21B6"},
}

_CATEGORY_LABELS = {
    "OTC": "Over-the-Counter", "PRESCRIPTION": "Prescription",
    "HIGH_RISK": "High-Risk", "ANTIBIOTIC": "Antibiotic", "CONTROLLED": "Controlled",
}


def _get_drug_category(drug_name: str) -> str:
    d = (drug_name or "").lower().strip()
    if d in HIGH_RISK_DRUGS:
        return "HIGH_RISK"
    if d in CONTROLLED_DRUGS:
        return "CONTROLLED"
    if d in ANTIBIOTIC_DRUGS:
        return "ANTIBIOTIC"
    if d in PRESCRIPTION_DRUGS:
        return "PRESCRIPTION"
    return "OTC"


def _get_pill_svg(category: str) -> str:
    colors = CATEGORY_COLORS.get(category, CATEGORY_COLORS["OTC"])
    fill, stroke = colors["fill"], colors["stroke"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60" width="60" height="60">
  <ellipse cx="30" cy="52" rx="20" ry="4" fill="rgba(0,0,0,0.15)"/>
  <path d="M10 30 C10 19 18 12 30 12 L30 48 C18 48 10 41 10 30" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
  <path d="M30 12 C42 12 50 19 50 30 C50 41 42 48 30 48 L30 12" fill="#FFFFFF" stroke="{stroke}" stroke-width="1.5"/>
  <line x1="30" y1="12" x2="30" y2="48" stroke="{stroke}" stroke-width="1"/>
  <ellipse cx="20" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.35)"/>
  <ellipse cx="40" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.5)"/>
</svg>'''


# ── All known drugs list ─────────────────────────────────────────────────────

ALL_KNOWN_DRUGS: list[str] = sorted(set(
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

CONFIDENCE_THRESHOLD = 0.35
NEAR_EXACT_THRESHOLD = 0.95


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_structured(answer_text: str, question: str = "") -> StructuredAnswer:
    """Parse answer text into a StructuredAnswer model via domain.verdicts."""
    intent = _classify_query_intent(question) if question else ""
    drugs = extract_drug_names(question) if question else []
    raw = parse_structured_answer(
        answer_text, question,
        query_intent=intent,
        drug_names=drugs,
        all_known_drugs=ALL_KNOWN_DRUGS,
        extract_drug_names_fn=extract_drug_names,
    )
    return StructuredAnswer(**raw)


async def _log_search(query: str, matched_question_id: int | None) -> None:
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
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


async def _save_question_to_db(question: str, answer: str, category: str) -> int | None:
    logger.info("[Self-Learning] Saving new question: %.80s", question)
    try:
        async with async_engine.begin() as conn:
            r = await conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM questions"))
            next_id = int(r.scalar() or 0) + 1

            q_lower = question.lower()
            tags = [category.lower()]
            for drug in ["ibuprofen", "tylenol", "acetaminophen", "aspirin", "naproxen",
                         "amoxicillin", "metformin", "lisinopril", "omeprazole", "gabapentin",
                         "sertraline", "xanax", "alprazolam", "prednisone", "advil", "aleve"]:
                if drug in q_lower:
                    tags.append(drug)

            await conn.execute(
                questions_table.insert().values(
                    id=next_id, question=question, category=category,
                    tags=tags, answer=answer, created_at=_utc_now(),
                )
            )

        logger.info("[Self-Learning] Saved question #%d", next_id)
        try:
            from ml.tfidf_search import rebuild_index

            rebuild_index()
        except Exception as e:
            logger.warning("[Self-Learning] TF-IDF rebuild failed: %s", e)
        return next_id
    except Exception as e:
        logger.error("[Self-Learning] Failed: %s", e, exc_info=True)
        return None


async def _build_dataset_result(query: str) -> QuestionMatch | None:
    """Check CSV / DB cache for side-effects data."""
    query_lower = (query or "").strip().lower()
    if not query_lower:
        return None

    try:
        from data.drug_csv_loader import get_drug_by_generic, load_drug_lookup
    except ImportError:
        return None

    dataset_drug_name: str | None = None
    for candidate in load_drug_lookup():
        if candidate in query_lower:
            dataset_drug_name = candidate
            break

    if not dataset_drug_name:
        return None

    # DB-first: structured cache
    try:
        from pipeline.side_effects_store import _DRUG_ALIASES, get_from_db

        db_key = _DRUG_ALIASES.get(dataset_drug_name, dataset_drug_name)
        db_result = get_from_db(db_key)
        if db_result:
            pubmed_studies = await fetch_pubmed_studies(dataset_drug_name)
            se_tiers = db_result.get("side_effects", {})
            common_names = ", ".join(
                e.get("display_name", "")
                for tier_key in ("very_common", "common")
                for e in se_tiers.get(tier_key, {}).get("items", [])
            )
            serious_names = [e.get("display_name", "") for e in se_tiers.get("serious", {}).get("items", [])]
            return QuestionMatch(
                id=0, question=query, category="side_effects",
                tags=[dataset_drug_name, "db_cache"], score=1.0,
                answer=common_names or db_key,
                structured=StructuredAnswer(
                    answer=common_names or db_key, short_answer=db_key,
                    sources="db_cache", intent="side_effects", drug=db_key,
                    generic_name=db_result.get("generic_name", db_key),
                    brand_names=db_result.get("brand_names", []),
                    side_effects_data=se_tiers,
                    boxed_warnings=db_result.get("boxed_warnings", []),
                    mechanism_of_action=db_result.get("mechanism_of_action", {}),
                    structured_sources=db_result.get("sources", []),
                    common_side_effects=common_names,
                    serious_side_effects=serious_names,
                    pubmed_studies=pubmed_studies,
                ),
            )
    except Exception:
        pass

    # CSV fallback
    drug = get_drug_by_generic(dataset_drug_name)
    if not drug:
        return None

    side_effects = str(drug.get("side_effects_simple", "")).strip()
    mechanism = str(drug.get("mechanism_simple", "")).strip()
    pubmed_studies = await fetch_pubmed_studies(dataset_drug_name)

    return QuestionMatch(
        id=0, question=query, category="side_effects",
        tags=[dataset_drug_name, "dataset"], score=1.0,
        answer=side_effects,
        structured=StructuredAnswer(
            answer=side_effects, short_answer=side_effects,
            article=mechanism, sources="dataset", intent="side_effects",
            drug=dataset_drug_name, common_side_effects=side_effects,
            mechanism=mechanism, pubmed_studies=pubmed_studies,
        ),
    )


# ── Full AI answer generation (orchestrates fda_client + claude_client) ──────

async def _generate_full_answer(question: str) -> tuple[str, list[dict], str, str]:
    """Orchestrate FDA fetch → context build → Claude generation."""
    from answer_engine import (
        QuestionIntent,
        RetrievalStatus,
        build_citations,
        build_emergency_answer,
        build_unknown_drug_answer,
        check_high_risk_pair,
        check_retrieval_guard,
        detect_emergency,
        extract_fda_metadata,
    )
    from drug_catalog import find_drug, is_high_risk as catalog_is_high_risk, is_known_drug
    from services.claude_client import generate_ai_answer

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    # Emergency
    if detect_emergency(question):
        emergency = build_emergency_answer(fetched_at)
        return (
            f"VERDICT: EMERGENCY\n"
            f"ANSWER: {emergency.short_answer}\n"
            f"WARNING: This is a medical emergency — do not wait.\n"
            f"DETAILS: Immediate action is required | Do not induce vomiting unless directed by poison control | Stay on the line with emergency services\n"
            f"ACTION: {' | '.join(emergency.emergency_escalation)}\n"
            f"ARTICLE: If you or someone else is experiencing a medical emergency related to medication, call 911 or Poison Control (1-800-222-1222) immediately.\n"
            f"CONFIDENCE: HIGH\n"
            f"SOURCES: Emergency Services"
        ), [], QuestionIntent.GENERAL.value, RetrievalStatus.REFUSED_NO_SOURCE.value

    drug_names = extract_drug_names(question)
    normalised = []
    for raw_name in drug_names:
        rec = find_drug(raw_name)
        normalised.append(rec.canonical_name if rec else raw_name)
    drug_names = normalised
    drug_name = drug_names[0] if drug_names else extract_drug_name(question)

    # Unknown drug
    if drug_name and not is_known_drug(drug_name):
        fda_check, _ = await fetch_fda_label_with_raw(drug_name)
        if not fda_check:
            unknown = build_unknown_drug_answer(drug_name, fetched_at)
            return (
                f"VERDICT: CONSULT_PHARMACIST\n"
                f"ANSWER: {unknown.short_answer}\n"
                f"WARNING: This drug was not found in our database.\n"
                f"DETAILS: Drug name not found in FDA database | Safety information unavailable | A pharmacist can look up the full drug record\n"
                f"ACTION: {' | '.join(unknown.what_to_do)}\n"
                f"ARTICLE: RxBuddy could not find FDA label information for this drug name.\n"
                f"CONFIDENCE: LOW\n"
                f"SOURCES: RxNorm (rxnav.nlm.nih.gov)"
            ), [], QuestionIntent.GENERAL.value, RetrievalStatus.LABEL_NOT_FOUND.value

    intent_str = _classify_query_intent(question)
    intent_enum = QuestionIntent(intent_str) if intent_str in QuestionIntent._value2member_map_ else QuestionIntent.GENERAL
    risky_pair = check_high_risk_pair(drug_names)

    # Fetch labels
    if len(drug_names) >= 2:
        all_labels = await fetch_all_labels(drug_names)
        fda_data, raw_label = all_labels.get(drug_name, (None, None)) if drug_name else (None, None)
    else:
        all_labels = {}
        fda_data, raw_label = await fetch_fda_label_with_raw(drug_name) if drug_name else (None, None)

    proceed, retrieval_status_enum = check_retrieval_guard(intent_enum, fda_data, drug_names, question)
    retrieval_status_str = retrieval_status_enum.value
    cit_objects = build_citations(fda_data, raw_label, drug_name or "", intent_enum, fetched_at)
    citations_dicts = [c.model_dump() for c in cit_objects]

    # Deterministic
    det = lookup_deterministic(drug_names, intent_str)
    if det:
        det_verdict, det_direct = det
        return (
            f"VERDICT: {det_verdict}\n"
            f"ANSWER: {det_direct}\n"
            f"WARNING: This combination is pre-classified as {det_verdict}.\n"
            f"DETAILS: Identified in clinical pharmacology references | FDA label confirms interaction | Individual risk may vary\n"
            f"ACTION: Follow prescriber instructions | Do not change doses without consulting provider | Read the official drug label\n"
            f"ARTICLE: This combination has been pre-classified based on FDA-approved drug label data.\n"
            f"CONFIDENCE: HIGH\n"
            f"SOURCES: FDA label (DailyMed) | Clinical pharmacology guidelines"
        ), citations_dicts, intent_str, retrieval_status_str

    medlineplus_data = await fetch_medlineplus(drug_name) if drug_name else None

    if len(drug_names) >= 2 and all_labels:
        fda_context = build_multi_drug_context(all_labels, question, medlineplus_data)
    else:
        fda_context = build_fda_context(fda_data, question, medlineplus_data, intent_str)

    ext_sources = await fetch_all_external_sources(drug_name or "", drug_names, intent_str)
    enriched = build_enriched_context(ext_sources, intent_str)
    if enriched:
        fda_context = (fda_context + "\n\n" + enriched).strip() if fda_context else enriched

    for src_label in ext_sources.get("sources_used", []):
        citations_dicts.append({
            "id": f"ext_{len(citations_dicts)}", "source": src_label,
            "source_url": "", "section": "external",
            "section_label": src_label, "drug_name": drug_name or "",
            "date_fetched": fetched_at,
        })

    return await generate_ai_answer(
        question=question,
        drug_names=drug_names,
        drug_name=drug_name,
        intent_str=intent_str,
        fda_context=fda_context,
        citations_dicts=citations_dicts,
        retrieval_status_str=retrieval_status_str,
        proceed=proceed,
        risky_pair=risky_pair,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "RxBuddy is live"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/search", response_model=SearchResponse)
@limiter.limit("10/minute")
async def search(request: Request, req: SearchRequest) -> SearchResponse:
    user_query = req.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # Non-drug gate
    rejection = _check_non_drug_query(user_query)
    if rejection:
        return SearchResponse(
            query=user_query,
            results=[QuestionMatch(
                id=0, question=user_query, category="non_drug_query",
                tags=["rejected"], score=0.0, answer=rejection["message"],
                structured=StructuredAnswer(
                    answer=rejection["message"], short_answer=rejection["message"],
                    intent=rejection["intent"], verdict="NON_DRUG", sources="filter",
                ),
            )],
            source="filter",
        )

    dataset_result = await _build_dataset_result(user_query)
    if dataset_result:
        await _log_search(user_query, None)
        return SearchResponse(query=user_query, results=[dataset_result], source="dataset")

    # Drug resolver rewrite
    try:
        from services.drug_resolver import resolve_query_drugs

        _resolved = resolve_query_drugs(user_query)
        if _resolved:
            _rewritten = user_query
            for _rd in _resolved:
                _inp, _gen, _cor = _rd.get("input", ""), _rd.get("generic", ""), _rd.get("corrected", "")
                if _cor and _cor.lower() != _inp.lower() and _cor.lower() in _rewritten.lower():
                    _rewritten = re.sub(re.escape(_cor), _gen, _rewritten, flags=re.IGNORECASE)
            if _rewritten != user_query:
                user_query = _rewritten
    except Exception:
        pass

    from ml.tfidf_search import find_exact_match, search as tfidf_search

    exact_match = find_exact_match(user_query)
    if exact_match and exact_match.answer:
        if not is_corrupted_db_answer(exact_match.answer) and not is_wrong_drug_answer(user_query, exact_match.answer, extract_drug_names):
            await _log_search(user_query, exact_match.id)
            return SearchResponse(
                query=user_query,
                results=[QuestionMatch(
                    id=exact_match.id, question=exact_match.question,
                    category=exact_match.category, tags=exact_match.tags,
                    score=1.0, answer=exact_match.answer,
                    structured=_to_structured(exact_match.answer, exact_match.question),
                )],
                source="database",
            )

    original_query, cleaned_query = normalize_query(user_query)
    search_query = cleaned_query or user_query
    did_you_mean = spell_check_query(search_query)

    drug_names = extract_drug_names(search_query)
    query_intent = _classify_query_intent(search_query)
    enhanced_query = _build_intent_query(search_query, query_intent, drug_names)

    engine_name = (req.engine or "tfidf").strip().lower()
    top_k = int(req.top_k)

    if engine_name == "tfidf":
        matches = tfidf_search(search_query, top_k=top_k)
        if enhanced_query != search_query:
            extra = tfidf_search(enhanced_query, top_k=top_k)
            seen = {m.id: m for m in matches}
            for m in extra:
                if m.id not in seen or float(m.score) > float(seen[m.id].score):
                    seen[m.id] = m
            matches = list(seen.values())
        matches = _rerank_by_intent(matches, drug_names, query_intent)
        match_ids = [m.id for m in matches]
        score_by_id = {m.id: float(m.score) for m in matches}
        answer_by_id = {m.id: m.answer for m in matches}

        if matches:
            best_q_lower = (matches[0].question or "").lower()
            user_q_lower = search_query.lower()
            missing_drugs = [d for d in drug_names if d not in best_q_lower]
            query_concepts = [c for c in _REQUIRED_CONCEPT_WORDS if c in user_q_lower]
            missing_concepts = [c for c in query_concepts if c not in best_q_lower]
            if missing_drugs or missing_concepts:
                match_ids, score_by_id, answer_by_id = [], {}, {}
    elif engine_name == "knn":
        from ml.knn_search import search as knn_search

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

    best_score = max(score_by_id.values()) if score_by_id else 0.0
    source = "database"
    saved_to_db = False

    # Near-exact
    if best_score >= NEAR_EXACT_THRESHOLD and match_ids:
        best_id = match_ids[0]
        cached_answer = answer_by_id.get(best_id)
        if cached_answer:
            try:
                async with async_engine.connect() as conn:
                    row = (await conn.execute(
                        select(questions_table.c.id, questions_table.c.question,
                               questions_table.c.category, questions_table.c.tags,
                               questions_table.c.answer)
                        .where(questions_table.c.id == best_id)
                    )).mappings().first()
                if row and row.get("answer"):
                    ans = str(row["answer"])
                    if not is_corrupted_db_answer(ans) and not is_wrong_drug_answer(user_query, ans, extract_drug_names):
                        await _log_search(user_query, best_id)
                        return SearchResponse(
                            query=user_query,
                            results=[QuestionMatch(
                                id=int(row["id"]), question=str(row["question"]),
                                category=row.get("category"),
                                tags=[str(t) for t in (row.get("tags") or [])],
                                score=best_score, answer=ans,
                                structured=_to_structured(ans, str(row["question"])),
                            )],
                            did_you_mean=did_you_mean, source="database",
                        )
            except Exception:
                pass

    # Low confidence → Claude
    if best_score < CONFIDENCE_THRESHOLD or not match_ids:
        if settings.has_anthropic_key:
            try:
                live_answer, live_cits, live_intent, live_rs = await _generate_full_answer(original_query)
                source = "ai_generated"

                def _enrich(sa: StructuredAnswer) -> StructuredAnswer:
                    sa.citations = live_cits
                    sa.intent = live_intent
                    sa.retrieval_status = live_rs
                    return sa

                from services.claude_client import get_best_category, is_valid_pharmacy_question

                if await is_valid_pharmacy_question(original_query):
                    category = await get_best_category(original_query, PHARMACY_CATEGORIES)
                    new_id = await _save_question_to_db(original_query, live_answer, category)
                    if new_id:
                        saved_to_db = True
                        await _log_search(user_query, new_id)
                        return SearchResponse(
                            query=original_query,
                            results=[QuestionMatch(
                                id=new_id, question=original_query, category=category,
                                tags=[category.lower()], score=1.0, answer=live_answer,
                                structured=_enrich(_to_structured(live_answer, original_query)),
                            )],
                            did_you_mean=did_you_mean, source=source, saved_to_db=True,
                        )

                await _log_search(user_query, None)
                return SearchResponse(
                    query=original_query,
                    results=[QuestionMatch(
                        id=0, question=original_query, category="General",
                        tags=[], score=best_score, answer=live_answer,
                        structured=_enrich(_to_structured(live_answer, original_query)),
                    )],
                    did_you_mean=did_you_mean, source=source,
                )
            except Exception as exc:
                logger.error("[Claude] Failed: %s", exc, exc_info=True)

    if not match_ids:
        return SearchResponse(query=original_query, results=[], did_you_mean=did_you_mean, source=source)

    # Fetch DB rows
    try:
        async with async_engine.connect() as conn:
            rows = (await conn.execute(
                select(questions_table.c.id, questions_table.c.question,
                       questions_table.c.category, questions_table.c.tags,
                       questions_table.c.answer)
                .where(questions_table.c.id.in_(match_ids))
            )).mappings().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error. ({e})")

    row_by_id = {int(r["id"]): dict(r) for r in rows}
    results: list[QuestionMatch] = []
    generated_answers: dict[int, str] = {}

    for qid in match_ids:
        r = row_by_id.get(int(qid))
        if not r:
            continue
        answer_text = str(r.get("answer") or "").strip() or None
        if answer_text and is_corrupted_db_answer(answer_text):
            answer_text = None
        elif answer_text and is_wrong_drug_answer(user_query, answer_text, extract_drug_names):
            answer_text = None
        if not answer_text and settings.has_anthropic_key:
            try:
                answer_text, _, _, _ = await _generate_full_answer(str(r["question"]))
                generated_answers[int(r["id"])] = answer_text
            except Exception:
                answer_text = None

        results.append(QuestionMatch(
            id=int(r["id"]), question=str(r["question"]),
            category=r.get("category"), tags=[str(t) for t in (r.get("tags") or [])],
            score=score_by_id.get(int(qid)), answer=answer_text,
            structured=_to_structured(answer_text, user_query) if answer_text else None,
        ))

    if generated_answers:
        try:
            async with async_engine.begin() as conn:
                for qid, ans in generated_answers.items():
                    await conn.execute(
                        questions_table.update().where(questions_table.c.id == int(qid)).values(answer=ans)
                    )
        except Exception:
            pass

    await _log_search(user_query, match_ids[0] if match_ids else None)
    return SearchResponse(query=original_query, results=results, did_you_mean=did_you_mean, source=source, saved_to_db=saved_to_db)


@router.post("/search/stream")
async def search_stream(req: SearchRequest) -> StreamingResponse:
    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, default=str)}\n\n"

    async def generate():
        user_query = req.query.strip()
        if not user_query:
            yield _sse({"type": "error", "message": "Query cannot be empty."})
            return

        dataset_result = await _build_dataset_result(user_query)
        if dataset_result:
            yield _sse({"type": "done", "source": "dataset", "result": dataset_result.model_dump()})
            return

        yield _sse({"type": "status", "message": "Searching database..."})

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

        from ml.tfidf_search import find_exact_match, search as tfidf_search

        exact = find_exact_match(user_query)
        if exact and exact.answer and not is_corrupted_db_answer(exact.answer) and not is_wrong_drug_answer(user_query, exact.answer, extract_drug_names):
            yield _sse({"type": "done", "source": "database", "result": {
                "id": exact.id, "question": exact.question, "category": exact.category,
                "tags": exact.tags or [], "score": 1.0, "answer": exact.answer,
                "structured": _to_structured(exact.answer, exact.question),
            }})
            return

        original_query, cleaned_query = normalize_query(user_query)
        search_query = cleaned_query or user_query
        drug_names = extract_drug_names(search_query)
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
                    async with async_engine.connect() as conn:
                        row = (await conn.execute(
                            select(questions_table).where(questions_table.c.id == int(best_id))
                        )).mappings().first()
                    if row:
                        ans = str(row.get("answer") or "").strip() or None
                        if ans and not is_corrupted_db_answer(ans) and not is_wrong_drug_answer(user_query, ans, extract_drug_names):
                            yield _sse({"type": "done", "source": "database", "result": {
                                "id": int(row["id"]), "question": str(row["question"]),
                                "category": row.get("category"), "tags": list(row.get("tags") or []),
                                "score": best_score, "answer": ans,
                                "structured": _to_structured(ans, user_query),
                            }})
                            return
                except Exception:
                    pass

        if not settings.has_anthropic_key:
            yield _sse({"type": "error", "message": "AI unavailable — no API key."})
            return

        yield _sse({"type": "status", "message": "Generating answer with AI..."})
        try:
            answer_text, citations, intent_str, rs = await _generate_full_answer(original_query)
            yield _sse({"type": "done", "source": "ai_generated", "result": {
                "id": None, "question": original_query, "category": None,
                "tags": [], "score": 1.0, "answer": answer_text,
                "structured": _to_structured(answer_text, original_query),
            }})
        except Exception:
            yield _sse({"type": "error", "message": "Could not generate an answer."})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/answer", response_model=AnswerResponse)
async def answer(req: AnswerRequest) -> AnswerResponse:
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="question cannot be empty")
    try:
        a, _, _, _ = await _generate_full_answer(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Claude error. ({e})")
    return AnswerResponse(question=q, answer=a)


@router.post("/v2/search")
@limiter.limit("10/minute")
async def search_v2(request: Request, req: SearchRequest) -> JSONResponse:
    from pipeline.orchestrator import run_pipeline

    user_query = req.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    dataset_result = await _build_dataset_result(user_query)
    if dataset_result:
        return JSONResponse(content=SearchResponse(
            query=user_query, results=[dataset_result], source="dataset",
        ).model_dump())

    try:
        result = await run_pipeline(user_query)
        return JSONResponse(content=result)
    except Exception as exc:
        logger.error("[v2/search] Pipeline failed: %s", exc, exc_info=True)
        from pipeline.failsafe import build_failsafe_response

        return JSONResponse(status_code=200, content=build_failsafe_response(user_query, str(exc)))


@router.post("/v2/search/stream")
async def search_v2_stream(req: SearchRequest) -> StreamingResponse:
    from pipeline.orchestrator import run_pipeline
    from pipeline.classifier import is_emergency
    from pipeline.cache import cache_get
    from pipeline.drug_extractor import normalize_query as v2_normalize

    async def generate():
        user_query = req.query.strip()
        if not user_query:
            yield _sse_v2({"type": "error", "message": "Query cannot be empty."})
            return

        dataset_result = await _build_dataset_result(user_query)
        if dataset_result:
            yield _sse_v2({"type": "done", "source": "dataset", "result": dataset_result.model_dump()})
            return

        if is_emergency(user_query):
            from pipeline.failsafe import build_emergency_response

            result = build_emergency_response(user_query)
            yield _sse_v2({"type": "done", "source": "emergency", "result": result["results"][0] if result["results"] else {}})
            return

        yield _sse_v2({"type": "status", "message": "Checking cache..."})
        _, cleaned = v2_normalize(user_query)
        cached = cache_get(cleaned)
        if cached:
            yield _sse_v2({"type": "done", "source": "cache", "result": cached["results"][0] if cached.get("results") else {}})
            return

        yield _sse_v2({"type": "status", "message": "Analyzing your question..."})
        try:
            result = await run_pipeline(user_query)
            source = result.get("source", "pipeline_v2")
            if result.get("verdict") == "NON_DRUG" or result.get("intent") == "non_drug_query":
                yield _sse_v2({"type": "done", "source": "pipeline_v2", "result": result})
            else:
                yield _sse_v2({"type": "done", "source": source, "result": result["results"][0] if result.get("results") else {}})
        except Exception as exc:
            logger.error("[v2/stream] Pipeline failed: %s", exc, exc_info=True)
            from pipeline.failsafe import build_failsafe_response

            fallback = build_failsafe_response(user_query, str(exc))
            yield _sse_v2({"type": "done", "source": "failsafe", "result": fallback["results"][0] if fallback.get("results") else {}})

    def _sse_v2(payload: dict) -> str:
        return f"data: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/v2/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat_v2(request: Request, req: ChatRequest) -> ChatResponse:
    if not settings.has_anthropic_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")

    drug = req.drug_name.strip() or "this medication"
    normalized_q = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", req.message.lower().strip()))
    normalized_drug = drug.lower().strip()

    # Cache check
    try:
        async with async_engine.connect() as conn:
            row = (await conn.execute(
                select(drug_chat_cache.c.answer).where(
                    drug_chat_cache.c.drug_name == normalized_drug,
                    drug_chat_cache.c.question == normalized_q,
                )
            )).first()
            if row:
                return ChatResponse(reply=row[0])
    except Exception:
        pass

    system_prompt = (
        f"You are RxBuddy, a friendly medication assistant. The user is asking about {drug}.\n\n"
        f"Your job: Answer every question like the user is 15 years old.\n\n"
        f"STRICT RULES:\n- Keep answers UNDER 30 words\n- Use VERY simple language\n"
        f"- No medical jargon unless necessary\n- No long explanations\n"
        f"- 1-2 short sentences max\n\n"
        f"TONE: Calm, clear, helpful.\n\nFORMAT: Plain text only.\n\n"
        f"If asked about something unrelated to {drug}, redirect back.\n"
        f"If unsure, say: \"I'm not sure--please ask a doctor or pharmacist.\""
    )

    messages = [{"role": msg.role, "content": msg.content} for msg in req.conversation_history[-6:]]
    messages.append({"role": "user", "content": req.message})

    try:
        from services.claude_client import _get_client

        client = _get_client()
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=messages,
        )
        reply_text = response.content[0].text if response.content else "Sorry, I couldn't generate a response."
    except Exception as exc:
        logger.error("[Chat] Claude API error: %s", exc)
        return ChatResponse(reply="I'm having trouble connecting. Please try again.")

    try:
        async with async_engine.begin() as conn:
            await conn.execute(insert(drug_chat_cache).values(
                drug_name=normalized_drug, question=normalized_q, answer=reply_text,
            ))
    except Exception:
        pass

    return ChatResponse(reply=reply_text)


@router.get("/drug-image", response_model=DrugImageResponse)
async def get_drug_image(name: str) -> DrugImageResponse:
    drug_name = name.strip().lower() if name else ""
    generic_name = BRAND_TO_GENERIC.get(drug_name, drug_name)
    category = _get_drug_category(generic_name)
    return DrugImageResponse(
        drug_name=generic_name or "unknown", category=category,
        category_label=_CATEGORY_LABELS.get(category, "Over-the-Counter"),
        svg_data=_get_pill_svg(category),
    )


@router.get("/pill-image", response_model=PillImageResponse)
async def get_pill_image_endpoint(name: str) -> PillImageResponse:
    drug_name = name.strip().lower() if name else ""
    if not drug_name:
        raise HTTPException(status_code=400, detail="name parameter is required.")
    generic_name = BRAND_TO_GENERIC.get(drug_name, drug_name)
    image_url = await fetch_pill_image(generic_name)
    return PillImageResponse(drug_name=generic_name, image_url=image_url, source="rximage" if image_url else "fallback")


_drug_index_cache: dict[str, list] = {}


@router.get("/drug-index", response_model=DrugIndexResponse)
async def get_drug_index(letter: Optional[str] = None) -> DrugIndexResponse:
    cache_key = (letter or "ALL").upper()
    if cache_key in _drug_index_cache:
        cached = _drug_index_cache[cache_key]
        return DrugIndexResponse(letter=letter, total=len(cached), drugs=cached)

    filtered = ALL_KNOWN_DRUGS
    if letter:
        filtered = [d for d in ALL_KNOWN_DRUGS if d.upper().startswith(letter.strip().upper())]

    entries: list[DrugIndexEntry] = []
    for drug in filtered:
        category = _get_drug_category(drug)
        image_url = await fetch_pill_image(drug)
        entries.append(DrugIndexEntry(
            drug_name=drug, category=category,
            category_label=_CATEGORY_LABELS.get(category, "Over-the-Counter"),
            image_url=image_url, svg_data=_get_pill_svg(category),
        ))

    _drug_index_cache[cache_key] = entries
    return DrugIndexResponse(letter=letter, total=len(entries), drugs=entries)


@router.post("/log", response_model=LogResponse)
async def log_search_endpoint(req: LogRequest) -> LogResponse:
    q = req.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    try:
        async with async_engine.begin() as conn:
            result = await conn.execute(
                search_logs_table.insert().returning(search_logs_table.c.id),
                {"query": q, "matched_question_id": req.matched_question_id,
                 "clicked": bool(req.clicked), "session_id": req.session_id,
                 "searched_at": _utc_now()},
            )
            log_id = int(result.scalar_one())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error. ({e})")
    return LogResponse(ok=True, log_id=log_id)


@router.get("/admin/cache-stats")
async def admin_cache_stats() -> dict:
    import label_updater

    return label_updater.cache_stats()


@router.post("/admin/refresh-label")
async def admin_refresh_label(drug: str) -> dict:
    if not drug.strip():
        raise HTTPException(status_code=400, detail="drug parameter is required")
    drug_name = drug.strip().lower()
    try:
        import label_updater as _lu

        if drug_name in _lu._cache:
            del _lu._cache[drug_name]
    except Exception:
        pass

    fda_data, raw_label = await fetch_fda_label_with_raw(drug_name)
    if not fda_data:
        return {"refreshed": False, "drug": drug_name, "reason": "label not found"}

    import label_updater

    meta = label_updater.get_label_metadata(drug_name) or {}
    return {
        "refreshed": True, "drug": drug_name,
        "label_revision_date": meta.get("label_revision_date"),
        "date_fetched": meta.get("date_fetched"),
        "cache_expires_at": meta.get("cache_expires_at"),
    }


@router.get("/api/drugs/{drug_name}/side-effects")
async def drug_side_effects(drug_name: str) -> JSONResponse:
    from pipeline.api_layer import fetch_dailymed_setid, fetch_fda_label as pipeline_fetch_fda_label, parse_structured_side_effects
    from pipeline.side_effects_store import get_or_fetch_side_effects
    import asyncio

    name = drug_name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="drug_name cannot be empty")

    generic = BRAND_TO_GENERIC.get(name, name)

    try:
        (fda_label, raw_label), setid = await asyncio.gather(
            pipeline_fetch_fda_label(generic), fetch_dailymed_setid(generic),
        )
        result = await get_or_fetch_side_effects(generic, fda_label, raw_label, setid)
        if not result:
            result = parse_structured_side_effects(drug_name=generic, fda_label=fda_label, raw_label=raw_label, dailymed_setid=setid)

        se_data = result.get("side_effects", {})
        def _names(items): return [e.get("display_name", e) if isinstance(e, dict) else str(e) for e in items if e]
        common_items = se_data.get("very_common", {}).get("items", []) + se_data.get("common", {}).get("items", [])
        serious_items = se_data.get("serious", {}).get("items", [])

        return JSONResponse(content={
            "drug": generic, "requested_name": drug_name,
            "source": "fallback" if result.get("_fallback") else "dataset",
            "common_side_effects": _names(common_items),
            "serious_side_effects": _names(serious_items),
            "mechanism": result.get("mechanism_of_action", {}).get("summary", ""),
            "data": result,
            "disclaimer": "This information is from FDA drug labels and is not medical advice.",
        })
    except Exception:
        return JSONResponse(status_code=200, content={
            "drug": name, "requested_name": drug_name, "source": "fallback",
            "common_side_effects": [], "serious_side_effects": [], "mechanism": "", "data": None,
            "disclaimer": "Dataset temporarily unavailable.",
        })


@router.post("/api/drugs/{drug_name}/parse-label")
async def parse_drug_label(drug_name: str) -> JSONResponse:
    from pipeline.api_layer import fetch_dailymed_setid, fetch_fda_label as pipeline_fetch_fda_label
    from pipeline.side_effects_store import parse_label_with_gemini, store_to_db
    import asyncio

    name = drug_name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="drug_name cannot be empty")
    _check_parse_rate_limit(name)

    generic = BRAND_TO_GENERIC.get(name, name)
    (fda_label, raw_label), setid = await asyncio.gather(
        pipeline_fetch_fda_label(generic), fetch_dailymed_setid(generic),
    )

    if not fda_label:
        return JSONResponse(status_code=200, content={"status": "no_label", "drug": generic})

    parsed = parse_label_with_gemini(generic, fda_label)
    if not parsed:
        return JSONResponse(status_code=200, content={"status": "parse_failed", "drug": generic})

    if raw_label:
        openfda = raw_label.get("openfda", {})
        parsed["brand_names"] = list(openfda.get("brand_name", []))[:5]
        parsed["generic_name"] = (openfda.get("generic_name") or [generic])[0]
        eff_date = raw_label.get("effective_time", "")
        label_date = f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}" if eff_date and len(eff_date) >= 8 else ""
        if setid:
            parsed["sources"] = [{"id": 1, "name": f"DailyMed — {generic.title()} label",
                                   "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}",
                                   "section": "ADVERSE REACTIONS", "last_updated": label_date}]

    ok = store_to_db(generic, parsed)
    total = sum(len(t.get("items", [])) for t in parsed.get("side_effects", {}).values())
    return JSONResponse(content={
        "status": "ok" if ok else "store_failed", "drug": generic,
        "brand_names": parsed.get("brand_names", []),
        "side_effects_count": total, "stored_to_db": ok,
    })
