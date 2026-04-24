"""RxBuddy FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Keep backend/ import-compatible with existing sibling imports such as
# core, services, pipeline, data, and api.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.search import init_spell_checker, limiter, router
from core.config import settings
from core.db import ensure_tables
from services.fda_client import close_client as close_fda_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
logger = logging.getLogger("rxbuddy")

_BACKEND_DIR = Path(__file__).resolve().parent
_RXNORM_JSON = _BACKEND_DIR / "data" / "rxnorm_terms.json"

_EXTRA_ORIGINS = [
    "https://rxbuddy.vercel.app",
    "https://rxbuddy-git-main-omgohel-3379s-projects.vercel.app",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
]


def _normalize_origins(origins: Any) -> list[str]:
    """Accept comma-separated or list CORS settings and return clean strings."""
    if origins is None:
        return []
    if isinstance(origins, str):
        return [origin.strip() for origin in origins.split(",") if origin.strip()]
    if isinstance(origins, list) and all(isinstance(origin, str) for origin in origins):
        return [origin.strip() for origin in origins if origin.strip()]
    raise TypeError("CORS origins must be a list[str] or comma-separated string")


def _search_error_payload(request_id: str) -> dict[str, Any]:
    """Return the frontend-compatible fallback shape used for unexpected errors."""
    return {
        "query": "",
        "results": [
            {
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
                    "interaction_summary": {"avoid_pairs": [], "caution_pairs": []},
                    "citations": [],
                    "intent": "general",
                    "retrieval_status": "LABEL_NOT_FOUND",
                },
            }
        ],
        "did_you_mean": None,
        "source": "error",
        "saved_to_db": False,
        "request_id": request_id,
    }


async def _init_spell_checker_bg() -> None:
    """Load drug terms from local JSON first, then fall back to RxNorm."""
    terms: set[str] = set()

    if _RXNORM_JSON.exists():
        try:
            raw = await asyncio.to_thread(_RXNORM_JSON.read_text, encoding="utf-8")
            terms = set(json.loads(raw))
            logger.info("[SpellChecker] Loaded %d terms from %s", len(terms), _RXNORM_JSON.name)
        except Exception as exc:
            logger.warning("[SpellChecker] Failed to read %s: %s", _RXNORM_JSON.name, exc)

    if not terms:
        try:
            from services.fda_client import fetch_rxnorm_drug_names

            terms = await fetch_rxnorm_drug_names(limit=1000)
            logger.info("[SpellChecker] Fetched %d terms from RxNorm API", len(terms))
        except Exception as exc:
            logger.warning("[SpellChecker] RxNorm fetch failed: %s", exc)

    init_spell_checker(terms)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting RxBuddy API")

    await ensure_tables()

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

    asyncio.create_task(_init_spell_checker_bg())

    try:
        import keep_alive

        keep_alive.start()
    except Exception as exc:
        logger.warning("[KeepAlive] Could not start: %s", exc)

    yield

    logger.info("Shutting down RxBuddy API")
    await close_fda_client()


_settings_origins = _normalize_origins(getattr(settings, "cors_origins_list", []))
_combined_origins = list(dict.fromkeys(_settings_origins + _EXTRA_ORIGINS))

app = FastAPI(title="RxBuddy API", version="0.2.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "rxbuddy.fly.dev",
        "*.fly.dev",
        "localhost",
        "127.0.0.1",
        "testserver",
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_combined_origins,
    allow_origin_regex=r"https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Add request ID and process time headers to every HTTP response."""
    started_at = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - started_at) * 1000:.2f}"
    return response


@app.get("/health", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Fly health check endpoint."""
    return {"status": "ok"}


app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return validation failures with request IDs."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.warning(
        "Validation error path=%s request_id=%s errors=%s",
        request.url.path,
        request_id,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log server-side details and return a safe frontend-compatible error."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception(
        "Unhandled exception path=%s request_id=%s error=%s",
        request.url.path,
        request_id,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content=_search_error_payload(request_id),
        headers={"X-Request-ID": request_id},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
    )
