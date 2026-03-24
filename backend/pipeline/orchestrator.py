"""
RxBuddy Pipeline v2 — Main Orchestrator.

This is the single entry point for the refactored search pipeline.
It runs the 10-step pipeline in order:

  1. classify_fast(query)          → zero-AI intent classification
  2. check_cache(query)            → L1 in-memory + L2 PostgreSQL (7-day TTL)
  3. extract_drugs(query)          → drug names + normalization
  4. fetch_all_apis(drugs)         → async parallel API fetching (≤1.5s each)
  5. compute_verdict(intent, data) → deterministic backend decision (PRIMARY TRUTH)
  6. generate_explanation(verdict)  → Claude explanation ONLY (verdict locked)
  7. enforce_verdict(explanation)   → hard verdict enforcement
  8. clean_response(explanation)    → max 2 sentences, no fluff
  9. cache_result(query, response)  → store for 7 days
 10. return response

Total target: <2 seconds (cache hits <200ms).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from pipeline.classifier import Intent, classify_fast, is_emergency
from pipeline.drug_extractor import extract_drug_names, normalize_drug_names, normalize_query
from pipeline.api_layer import fetch_all, APIResults
from pipeline.decision_engine import compute_verdict, DecisionResult, EMERGENCY
from pipeline.claude_explainer import generate_explanation, Explanation
from pipeline.verdict_enforcer import enforce_verdict
from pipeline.response_cleaner import clean_response
from pipeline.cache import cache_get, cache_set
from pipeline.failsafe import build_failsafe_response, build_emergency_response

logger = logging.getLogger("rxbuddy.pipeline.orchestrator")


def _build_structured_answer(
    verdict: str,
    explanation: Explanation,
    decision: DecisionResult,
    intent: str,
    drug_names: list[str],
    api_results: APIResults,
    fetched_at: str,
) -> dict:
    """
    Build the StructuredAnswer dict matching the existing API contract.
    This is what the frontend expects.
    """
    # Build citations from FDA label metadata
    citations = []
    for drug, raw_label in api_results.fda_raw_labels.items():
        openfda = raw_label.get("openfda", {}) if raw_label else {}
        set_ids = openfda.get("set_id", [])
        set_id = set_ids[0] if set_ids else None
        app_numbers = openfda.get("application_number", [])
        nda = app_numbers[0] if app_numbers else None

        effective_time = str(raw_label.get("effective_time", "") or "")
        rev_date = None
        if len(effective_time) == 8 and effective_time.isdigit():
            rev_date = f"{effective_time[:4]}-{effective_time[4:6]}-{effective_time[6:8]}"

        source_url = (
            f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}"
            if set_id
            else "https://dailymed.nlm.nih.gov/dailymed/"
        )

        citations.append({
            "id": f"cit_{len(citations)}",
            "source": "DailyMed",
            "source_url": source_url,
            "section": "drug_interactions",
            "section_label": "Drug Interactions",
            "drug_name": drug,
            "set_id": set_id,
            "nda_number": nda,
            "label_revision_date": rev_date,
            "date_fetched": fetched_at,
        })

    # Add external source citations
    for src in api_results.sources_used:
        if src != "FDA Label":
            citations.append({
                "id": f"ext_{len(citations)}",
                "source": src,
                "source_url": "",
                "section": "external",
                "section_label": src,
                "drug_name": drug_names[0] if drug_names else "",
                "date_fetched": fetched_at,
            })

    # Build source string
    sources = " | ".join(api_results.sources_used) if api_results.sources_used else "Pharmacist consultation recommended"

    return {
        "verdict": verdict,
        "answer": explanation.answer,
        "short_answer": explanation.answer,
        "warning": explanation.warning,
        "details": explanation.details,
        "action": explanation.action,
        "article": explanation.article,
        # ── Side-effects structured fields (populated when intent == "side_effects") ──
        "common_side_effects": explanation.common_side_effects,
        "serious_side_effects": explanation.serious_side_effects,
        "warning_signs": explanation.warning_signs,
        "higher_risk_groups": explanation.higher_risk_groups,
        "what_to_do": explanation.what_to_do,
        "drugs": drug_names,
        # Legacy fields (kept for frontend backward compat)
        "direct": explanation.answer,
        "do": explanation.action,
        "avoid": [],
        "doctor": [explanation.warning] if explanation.warning else [],
        "raw": "",
        "confidence": decision.confidence,
        "sources": sources,
        "interaction_summary": decision.interaction_pairs,
        "citations": citations,
        "intent": intent,
        "retrieval_status": decision.retrieval_status,
    }


async def run_pipeline(query: str) -> dict:
    """
    Run the full 10-step pipeline for a user query.

    Returns a dict matching the SearchResponse API contract:
    {
      "query": str,
      "results": [{"id": int, "question": str, "category": str, "tags": [],
                    "score": float, "answer": str, "structured": {...}}],
      "did_you_mean": str | None,
      "source": str,
      "saved_to_db": bool,
    }
    """
    start_time = time.monotonic()
    logger.info("[Pipeline] START query=%.80s", query)

    if not query or not query.strip():
        return build_failsafe_response(query, "Empty query")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    # ── Step 0: Emergency check ───────────────────────────────────────────
    if is_emergency(query):
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info("[Pipeline] EMERGENCY detected (%.0fms)", elapsed)
        return build_emergency_response(query)

    # ── Step 1: Fast intent classification (pre-drug-count) ───────────────
    # We'll re-classify after drug extraction with the correct drug count
    original_query, cleaned_query = normalize_query(query)

    # ── Step 2: Check cache ───────────────────────────────────────────────
    cached = cache_get(cleaned_query)
    if cached:
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info("[Pipeline] CACHE HIT (%.0fms)", elapsed)
        return cached

    # ── Step 3: Extract and normalize drugs ───────────────────────────────
    try:
        drug_names = extract_drug_names(cleaned_query)
        drug_names = normalize_drug_names(drug_names)
        # Deduplicate while preserving order
        drug_names = list(dict.fromkeys(drug_names))
    except Exception as exc:
        logger.error("[Pipeline] Drug extraction failed: %s", exc)
        drug_names = []

    # ── Step 1 (redux): Re-classify with correct drug count ───────────────
    intent = classify_fast(cleaned_query, drug_count=len(drug_names))
    intent_str = intent.value
    primary_drug = drug_names[0] if drug_names else ""

    logger.info("[Pipeline] Intent=%s drugs=%s", intent_str, drug_names)

    # ── Step 4: Fetch all APIs in parallel ────────────────────────────────
    try:
        api_results = await fetch_all(drug_names, intent=intent_str)
    except Exception as exc:
        logger.error("[Pipeline] API fetch failed: %s", exc)
        api_results = APIResults()

    # ── Step 5: Compute verdict (PRIMARY TRUTH) ───────────────────────────
    try:
        decision = compute_verdict(
            intent=intent,
            drug_names=drug_names,
            api_results=api_results,
            query=cleaned_query,
        )
    except Exception as exc:
        logger.error("[Pipeline] Decision engine failed: %s", exc)
        return build_failsafe_response(query, f"Decision engine error: {exc}")

    verdict = decision.verdict
    logger.info("[Pipeline] Verdict=%s confidence=%s deterministic=%s",
                verdict, decision.confidence, decision.is_deterministic)

    # ── Step 6: Generate explanation via Claude ───────────────────────────
    # Skip Claude for deterministic verdicts (faster, no hallucination risk)
    if decision.is_deterministic:
        from pipeline.claude_explainer import _build_fallback
        explanation = _build_fallback(verdict, decision.reasoning, drug_names, intent_str)
        logger.info("[Pipeline] Deterministic — skipped Claude")
    else:
        try:
            explanation = generate_explanation(
                intent=intent_str,
                drug_names=drug_names,
                verdict=verdict,
                reasoning=decision.reasoning,
                fda_labels=api_results.fda_labels,
                rxnav_interactions=api_results.rxnav_interactions,
                adverse_events=api_results.adverse_events,
                recalls=api_results.recalls,
                query=original_query,
            )
        except Exception as exc:
            logger.error("[Pipeline] Claude failed: %s — using fallback", exc)
            from pipeline.claude_explainer import _build_fallback
            explanation = _build_fallback(verdict, decision.reasoning, drug_names, intent_str)

    # ── Step 7: Enforce verdict ───────────────────────────────────────────
    explanation = enforce_verdict(
        backend_verdict=verdict,
        explanation=explanation,
        drug_names=drug_names,
        intent=intent_str,
    )

    # ── Step 8: Clean response ────────────────────────────────────────────
    explanation = clean_response(explanation)

    # ── Build the final structured response ───────────────────────────────
    structured = _build_structured_answer(
        verdict=verdict,
        explanation=explanation,
        decision=decision,
        intent=intent_str,
        drug_names=drug_names,
        api_results=api_results,
        fetched_at=fetched_at,
    )

    # Build full answer text for legacy consumers
    answer_text = (
        f"VERDICT: {verdict}\n"
        f"ANSWER: {explanation.answer}\n"
        f"WARNING: {explanation.warning}\n"
        f"DETAILS: {' | '.join(explanation.details)}\n"
        f"ACTION: {' | '.join(explanation.action)}\n"
        f"ARTICLE: {explanation.article}\n"
        f"CONFIDENCE: {decision.confidence}\n"
        f"SOURCES: {structured['sources']}"
    )

    response = {
        "query": original_query,
        "results": [{
            "id": 0,
            "question": original_query,
            "category": "General",
            "tags": [],
            "score": 1.0,
            "answer": answer_text,
            "structured": structured,
        }],
        "did_you_mean": None,
        "source": "pipeline_v2",
        "saved_to_db": False,
    }

    # ── Step 9: Cache result ──────────────────────────────────────────────
    try:
        cache_set(cleaned_query, response)
    except Exception as exc:
        logger.warning("[Pipeline] Cache set failed: %s", exc)

    elapsed = (time.monotonic() - start_time) * 1000
    logger.info("[Pipeline] DONE verdict=%s (%.0fms)", verdict, elapsed)

    return response


def run_pipeline_sync(query: str) -> dict:
    """
    Synchronous wrapper for run_pipeline().
    Use this from sync FastAPI endpoints or non-async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're already in an async context — create a new event loop in a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, run_pipeline(query))
            return future.result(timeout=15)
    else:
        return asyncio.run(run_pipeline(query))
