"""RxBuddy API — slim async entrypoint.

All route handlers live in api/search.py.
This file wires up the FastAPI app, middleware, lifespan, and error handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# Ensure backend/ is on sys.path so sibling modules (answer_engine,
# label_updater, keep_alive, etc.) can be imported regardless of how
# uvicorn is invoked (Railway, local, Procfile, …).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.search import init_spell_checker, limiter, router
from core.config import settings
from core.db import ensure_tables
from services.fda_client import close_client as close_fda_client

logger = logging.getLogger("rxbuddy")
logging.basicConfig(level=logging.INFO)

# ── Paths ────────────────────────────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent
_RXNORM_JSON = _BACKEND_DIR / "data" / "rxnorm_terms.json"


# ── Background spell-checker init (non-blocking startup) ────────────────────

async def _init_spell_checker_bg() -> None:
    """Load drug terms from local JSON first, fall back to async RxNorm fetch."""
    terms: set[str] = set()

    # 1. Try pre-built local JSON (fast, no network)
    if _RXNORM_JSON.exists():
        try:
            raw = await asyncio.to_thread(
                _RXNORM_JSON.read_text, encoding="utf-8",
            )
            terms = set(json.loads(raw))
            logger.info(
                "[SpellChecker] Loaded %d terms from %s",
                len(terms), _RXNORM_JSON.name,
            )
        except Exception as exc:
            logger.warning("[SpellChecker] Failed to read %s: %s", _RXNORM_JSON.name, exc)

    # 2. Fallback: fetch from RxNorm REST API
    if not terms:
        try:
            from services.fda_client import fetch_rxnorm_drug_names

            terms = await fetch_rxnorm_drug_names(limit=1000)
            logger.info("[SpellChecker] Fetched %d terms from RxNorm API", len(terms))
        except Exception as exc:
            logger.warning("[SpellChecker] RxNorm fetch failed: %s", exc)

    init_spell_checker(terms)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup / shutdown lifecycle."""

    # ── Startup ──────────────────────────────────────────────────────────
    # 1. Ensure DB tables exist
    await ensure_tables()

    # 2. Load drug CSV lookup (sync, small file)
    try:
        from data.drug_csv_loader import (
            DRUG_CSV_RELATIVE_PATH,
            drug_lookup_size,
            load_drug_lookup,
        )

        load_drug_lookup()
        logger.info(
            "[DrugCsvLoader] Loaded %d drugs from %s",
            drug_lookup_size(),
            DRUG_CSV_RELATIVE_PATH.as_posix(),
        )
    except Exception:
        logger.exception("[DrugCsvLoader] Failed to load CSV drug lookup")

    # 3. Spell checker in background task (non-blocking)
    asyncio.create_task(_init_spell_checker_bg())

    # 4. Keep-alive pinger (optional)
    try:
        import keep_alive

        keep_alive.start()
    except Exception as exc:
        logger.warning("[KeepAlive] Could not start: %s", exc)

    yield  # ── app is running ──

    # ── Shutdown ─────────────────────────────────────────────────────────
    await close_fda_client()


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="RxBuddy API", version="0.2.0", lifespan=lifespan)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"^https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(router)


# ── Global exception handler ────────────────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled_exception_handler(
    request: Request, exc: Exception,
) -> JSONResponse:
    """Catch-all: return structured JSON instead of a 500 HTML page."""
    logger.error(
        "[App] Unhandled exception on %s: %s",
        request.url.path, exc, exc_info=True,
    )
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
                    "direct": (
                        "We encountered an error processing your request. "
                        "Please try again or consult a pharmacist."
                    ),
                    "do": [],
                    "avoid": [],
                    "doctor": [],
                    "raw": "",
                    "confidence": "LOW",
                    "sources": "",
                    "interaction_summary": {
                        "avoid_pairs": [],
                        "caution_pairs": [],
                    },
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
