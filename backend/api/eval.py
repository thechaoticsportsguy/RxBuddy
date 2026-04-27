"""GET /api/eval/latest — public eval scoreboard.

Reads the newest evals/results_*.csv file at the project root, computes
accuracy / hallucination rate / citation rate, and serves the result with
a 24-hour in-memory cache. Returns a 200 with null values and
status="no_eval_run_yet" if no results file exists, so the public /trust
page can render an empty state instead of a 500.
"""

from __future__ import annotations

import csv
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("rxbuddy.api.eval")

router = APIRouter(prefix="/api/eval", tags=["eval"])

# Project root = repo top-level (where evals/ lives). This file is
# backend/api/eval.py, so .parent.parent.parent gets us to the root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_GLOB = "results_*.csv"
_RESULTS_DIR = _PROJECT_ROOT / "evals"
_TIMESTAMP_RE = re.compile(r"results_(\d{8}T\d{6}Z)\.csv$")

_GOLDSET_VERSION = "v1"
_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h

_cache_lock = Lock()
_cache: dict[str, Any] | None = None
_cache_expires_at: float = 0.0


def _parse_results_timestamp(path: Path) -> datetime | None:
    """Pull a UTC datetime out of `results_<UTC>.csv`."""
    m = _TIMESTAMP_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _find_latest_results() -> tuple[Path, datetime] | None:
    """Newest results CSV by filename timestamp. Falls back to mtime if name unparsable."""
    if not _RESULTS_DIR.exists():
        return None
    candidates: list[tuple[Path, datetime]] = []
    for path in _RESULTS_DIR.glob(_RESULTS_GLOB):
        ts = _parse_results_timestamp(path)
        if ts is None:
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        candidates.append((path, ts))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def _compute_metrics(csv_path: Path) -> dict[str, Any]:
    total = 0
    coverage_sum = 0.0
    hallucinations = 0
    citations = 0
    scored = 0

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total += 1
            kc_raw = (row.get("keyword_coverage") or "").strip()
            if not kc_raw:
                continue
            try:
                coverage_sum += float(kc_raw)
            except ValueError:
                continue
            scored += 1
            if (row.get("hallucination_present") or "").strip().lower() in {"true", "1"}:
                hallucinations += 1
            if (row.get("citation_correct") or "").strip().lower() in {"true", "1"}:
                citations += 1

    if scored == 0:
        return {
            "accuracy_pct": None,
            "hallucination_rate_pct": None,
            "citation_rate_pct": None,
            "total_questions": total,
            "scored_questions": 0,
        }

    return {
        "accuracy_pct": round(coverage_sum / scored * 100.0, 1),
        "hallucination_rate_pct": round(hallucinations / scored * 100.0, 1),
        "citation_rate_pct": round(citations / scored * 100.0, 1),
        "total_questions": total,
        "scored_questions": scored,
    }


def _build_payload() -> dict[str, Any]:
    found = _find_latest_results()
    if found is None:
        return {
            "accuracy_pct": None,
            "hallucination_rate_pct": None,
            "citation_rate_pct": None,
            "total_questions": 0,
            "last_run_at": None,
            "goldset_version": _GOLDSET_VERSION,
            "status": "no_eval_run_yet",
        }
    path, ts = found
    metrics = _compute_metrics(path)
    return {
        **metrics,
        "last_run_at": ts.isoformat(),
        "goldset_version": _GOLDSET_VERSION,
        "status": "ok",
    }


def _get_cached() -> dict[str, Any]:
    global _cache, _cache_expires_at
    now = time.time()
    with _cache_lock:
        if _cache is not None and now < _cache_expires_at:
            return _cache
        try:
            payload = _build_payload()
        except Exception:
            logger.exception("[eval/latest] failed to build payload")
            payload = {
                "accuracy_pct": None,
                "hallucination_rate_pct": None,
                "citation_rate_pct": None,
                "total_questions": 0,
                "last_run_at": None,
                "goldset_version": _GOLDSET_VERSION,
                "status": "error",
            }
        _cache = payload
        _cache_expires_at = now + _CACHE_TTL_SECONDS
        return payload


def reset_cache() -> None:
    """Test hook — flush the in-memory cache."""
    global _cache, _cache_expires_at
    with _cache_lock:
        _cache = None
        _cache_expires_at = 0.0


@router.get("/latest")
async def get_latest_eval() -> dict[str, Any]:
    """Public eval scoreboard. Cached for 24h in process memory."""
    return _get_cached()
