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
_medical_terms: set[str] = set()
_spell_checker: SpellChecker | None = None


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
    """
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            r = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM questions"))
            next_id = int(r.scalar() or 0) + 1

        # Extract tags from question (simple heuristic: drug names)
        q_lower = question.lower()
        tags = [category.lower()]
        # Add common drug names if found
        common_drugs = ["ibuprofen", "tylenol", "acetaminophen", "aspirin", "naproxen", "amoxicillin"]
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
        logger.info("[Self-Learning] Saved new question #%d: %.60s...", next_id, question)
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
    Generate a specific, actionable answer using Anthropic Claude.
    The answer is tailored to the exact question asked.
    Output is kept under ~200 words.
    """
    api_key = _anthropic_api_key()
    if not api_key:
        logger.error("[Claude] ANTHROPIC_API_KEY is NOT set in environment!")
        raise RuntimeError("ANTHROPIC_API_KEY is missing.")

    logger.info("[Claude] API key found (first 8 chars): %s...", api_key[:8] if len(api_key) > 8 else "***")

    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are a licensed pharmacist answering this SPECIFIC patient question. 
Give a DIRECT, SPECIFIC answer to exactly what they asked. Do NOT give generic medication advice.

Patient Question: {question}

Instructions:
1. Start with a clear YES/NO or direct answer to their specific question
2. Explain WHY with specific reasoning related to their question
3. Give 2-3 specific "What to Do" points for their situation
4. Give 2-3 specific "What to Avoid" points for their situation  
5. Give 1-2 "See a Doctor If" warning signs specific to their concern

Keep your answer under 200 words. Be specific to the drugs, conditions, or situations they mentioned.
Do not give generic advice like "follow package directions" - give actionable guidance for their exact question."""

        logger.info("[Claude] Sending question to claude-sonnet-4-20250514: %.80s...", question)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
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

        answer = _truncate_words(text, 200)
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


class QuestionMatch(BaseModel):
    id: int
    question: str
    category: Optional[str] = None
    tags: list[str] = []
    score: Optional[float] = None
    answer: Optional[str] = None


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
from ml.tfidf_search import search as tfidf_search
from ml.knn_search import search as knn_search


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    """
    Takes a user query and returns the top 5 matching questions.

    How it works:
    1) Normalize query (fix misspellings, slang, filler words) — zero API calls
    2) Run spell check and generate "did you mean?" suggestion
    3) Use TF-IDF + cosine similarity to find the best matching question IDs
    4) Check confidence threshold:
       - If best score >= 0.35 → return database results (source="database")
       - If best score < 0.35 → generate live Claude answer (source="ai_generated")
    5) Self-learning: if AI-generated and valid pharmacy question, save to DB
    """
    user_query = req.query.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

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
    elif engine_name == "knn":
        try:
            matches2 = knn_search(search_query, top_k=top_k)
            match_ids = [m.id for m in matches2]
            score_by_id = {m.id: float(m.score) for m in matches2}
        except (ModuleNotFoundError, ImportError):
            matches = tfidf_search(user_query, top_k=top_k)
            match_ids = [m.id for m in matches]
            score_by_id = {m.id: float(m.score) for m in matches}
    else:
        raise HTTPException(status_code=400, detail="engine must be 'tfidf' or 'knn'")

    # Get the best match score
    best_score = max(score_by_id.values()) if score_by_id else 0.0
    source = "database"
    saved_to_db = False

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

