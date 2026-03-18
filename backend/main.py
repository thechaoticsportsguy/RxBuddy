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
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
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


def _build_fda_context(fda_data: dict, question: str) -> str:
    """
    Build a context string from FDA label data for Claude.
    Only includes sections relevant to the question.
    """
    if not fda_data:
        return ""
    
    q_lower = question.lower()
    sections = []
    
    # Always include drug name
    sections.append(f"Drug: {fda_data.get('drug_name', 'Unknown')}")
    
    # Include relevant sections based on question keywords
    if any(w in q_lower for w in ["dose", "dosage", "how much", "how many", "take"]):
        if fda_data.get("dosage_and_administration"):
            sections.append(f"DOSAGE: {fda_data['dosage_and_administration'][:500]}")
    
    if any(w in q_lower for w in ["warning", "danger", "risk", "safe"]):
        if fda_data.get("warnings"):
            sections.append(f"WARNINGS: {fda_data['warnings'][:500]}")
    
    if any(w in q_lower for w in ["interact", "with", "mix", "combine", "together"]):
        if fda_data.get("drug_interactions"):
            sections.append(f"INTERACTIONS: {fda_data['drug_interactions'][:500]}")
    
    if any(w in q_lower for w in ["pregnant", "pregnancy", "breastfeed", "nursing"]):
        if fda_data.get("pregnancy"):
            sections.append(f"PREGNANCY: {fda_data['pregnancy'][:500]}")
    
    if any(w in q_lower for w in ["side effect", "reaction", "adverse"]):
        if fda_data.get("adverse_reactions"):
            sections.append(f"SIDE EFFECTS: {fda_data['adverse_reactions'][:500]}")
    
    if any(w in q_lower for w in ["shouldn't", "should not", "can't", "cannot", "avoid"]):
        if fda_data.get("contraindications"):
            sections.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:500]}")
    
    # If no specific sections matched, include warnings and dosage as default
    if len(sections) == 1:
        if fda_data.get("warnings"):
            sections.append(f"WARNINGS: {fda_data['warnings'][:400]}")
        if fda_data.get("dosage_and_administration"):
            sections.append(f"DOSAGE: {fda_data['dosage_and_administration'][:400]}")
    
    return "\n".join(sections)


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

    # TECHNIQUE 2: Ground answers in FDA data first
    drug_name = _extract_drug_name(question)
    fda_data = _fetch_fda_label(drug_name) if drug_name else None
    fda_context = _build_fda_context(fda_data, question) if fda_data else ""
    
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # System prompt for intent-classified medication answering
        system_prompt = """You are a medication question answering engine.

Your job is to answer the USER'S EXACT QUESTION only.
Do not answer a related question.
Do not answer based on one keyword alone.
Do not switch topics.
Do not confuse:
- drug interaction questions
- overdose questions
- side effect questions
- missed dose questions
- allergy questions
- pregnancy questions
- food/alcohol questions
- emergency symptom questions

ABSOLUTE RULE:
Before answering, you must classify the user's question into exactly one primary intent category.

VALID INTENT CATEGORIES:
1. Drug interaction / compatibility
2. Overdose / poisoning
3. Side effects
4. Missed dose
5. How to take / timing
6. Contraindication / when not to use
7. Food / alcohol interaction
8. Pregnancy / breastfeeding
9. Storage
10. General drug information
11. Emergency symptom triage
12. Unknown / ambiguous

STEP 1 — READ THE FULL QUESTION
STEP 2 — EXTRACT THE CORE ASK
STEP 3 — INTENT CHECK
STEP 4 — ANSWER THE EXACT QUESTION FIRST
STEP 5 — SAFETY FILTER
STEP 6 — CROSS-EXAMINATION CHECK
STEP 7 — CONTRADICTION BLOCK
STEP 8 — SIMPLICITY RULE

Output format EXACTLY:
Answer: [YES / NO / USUALLY YES / NEEDS REVIEW]
Why: [1-2 simple sentences]
Important notes: [only relevant bullets]
Get medical help now if: [only truly relevant urgent symptoms]

STEP 9 — DO NOT HALLUCINATE
STEP 10 — STRICT MISMATCH PREVENTION
STEP 11 — FINAL SELF-AUDIT"""

        if fda_context:
            prompt = f"""{system_prompt}

FDA LABEL DATA (use as primary source when relevant):
{fda_context}

PATIENT QUESTION: {question}"""
        else:
            prompt = f"""{system_prompt}

PATIENT QUESTION: {question}"""

        logger.info("[Claude] Sending question with FDA grounding to claude-sonnet-4-20250514: %.80s...", question)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,  # Increased for chain of thought
            messages=[{"role": "user", "content": prompt}],
        )

        logger.info("[Claude] Response received. Content blocks: %d", len(response.content))

        if not response.content:
            logger.error("[Claude] Response has no content blocks!")
            raise RuntimeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            logger.warning("[Claude] Empty text in response for question: %.80s", question)
            raise RuntimeError("Claude returned an empty response.")

        answer = _truncate_words(text, 300)  # Increased limit for new format
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
    direct: str = ""  # Direct YES/NO answer
    do: list[str] = []  # What to do bullets
    avoid: list[str] = []  # What to avoid bullets
    doctor: list[str] = []  # See a doctor if bullets
    raw: str = ""  # Original raw answer text
    confidence: str = "MEDIUM"  # HIGH, MEDIUM, or LOW
    sources: str = ""  # FDA label, DailyMed, etc.


def _parse_structured_answer(answer_text: str) -> StructuredAnswer:
    """
    Parse Claude's structured answer format into StructuredAnswer object.
    
    Expected format:
    DIRECT: [one sentence]
    DO: [item] | [item] | [item]
    AVOID: [item] | [item] | [item]
    DOCTOR: [item] | [item]
    
    Falls back gracefully if format doesn't match.
    """
    result = StructuredAnswer(raw=answer_text)
    
    if not answer_text:
        return result
    
    text = answer_text.strip()
    lines = text.split("\n")
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Parse DIRECT: line
        if line.upper().startswith("DIRECT:"):
            result.direct = line[7:].strip()
            
        # Parse DO: line
        elif line.upper().startswith("DO:"):
            items = line[3:].split("|")
            result.do = [item.strip() for item in items if item.strip()]
            
        # Parse AVOID: line
        elif line.upper().startswith("AVOID:"):
            items = line[6:].split("|")
            result.avoid = [item.strip() for item in items if item.strip()]
            
        # Parse DOCTOR: line
        elif line.upper().startswith("DOCTOR:"):
            items = line[7:].split("|")
            result.doctor = [item.strip() for item in items if item.strip()]
            
        # Parse CONFIDENCE: line
        elif line.upper().startswith("CONFIDENCE:"):
            conf = line[11:].strip().upper()
            if conf in ("HIGH", "MEDIUM", "LOW"):
                result.confidence = conf
            elif "HIGH" in conf:
                result.confidence = "HIGH"
            elif "LOW" in conf:
                result.confidence = "LOW"
            else:
                result.confidence = "MEDIUM"
                
        # Parse SOURCES: line
        elif line.upper().startswith("SOURCES:"):
            result.sources = line[8:].strip()
    
    # Fallback: if nothing was parsed, try to extract something useful
    if not result.direct and not result.do and not result.avoid:
        # Use first sentence as direct answer
        sentences = text.replace("\n", " ").split(".")
        if sentences:
            result.direct = sentences[0].strip() + "."
        
        # Provide generic fallbacks
        if not result.do:
            result.do = ["Follow your medication's specific dosing instructions"]
        if not result.avoid:
            result.avoid = ["Avoid exceeding the recommended dose"]
        if not result.doctor:
            result.doctor = ["Symptoms worsen or don't improve"]
    
    # Default confidence if not specified
    if not result.confidence:
        result.confidence = "MEDIUM"
    
    return result


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
                    structured=_parse_structured_answer(exact_match.answer),
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

    if engine_name == "tfidf":
        matches = tfidf_search(search_query, top_k=top_k)
        match_ids = [m.id for m in matches]
        score_by_id = {m.id: float(m.score) for m in matches}
        # Store answers from TF-IDF matches for near-exact check
        answer_by_id = {m.id: m.answer for m in matches}
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
                                structured=_parse_structured_answer(str(row["answer"])),
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
                                structured=_parse_structured_answer(live_answer),
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
                        structured=_parse_structured_answer(live_answer),
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
                structured=_parse_structured_answer(answer_text) if answer_text else None,
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

