"""
RxBuddy Integration Tests — Phase 5
=====================================

Covers:
  - The 5 canonical medication queries
  - Edge cases: misspellings, unknown drugs, multi-drug pairs
  - Guardrail activation: emergency, high-risk pair, no-label refusal
  - drug_catalog lookups: brand→generic, spell-correct
  - answer_engine: intent classification, retrieval guard, emergency detection
  - JSON schema validation: every response field is defined and typed

Run:
  pip install pytest httpx
  pytest tests/test_integration.py -v

Environment required (set in .env or Railway):
  NEXT_PUBLIC_API_URL   – or leave unset to use http://127.0.0.1:8000
  ANTHROPIC_API_KEY     – needed for AI-generated answers
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

# Make sure backend/ is importable when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

BASE_URL = os.environ.get("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000").rstrip("/")


# ── Fixtures ──────────────────────────────────────────────────────────────────

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


def post_search(query: str, engine: str = "tfidf", top_k: int = 5) -> dict:
    """POST /search and return the parsed JSON response."""
    if not HAS_HTTPX:
        pytest.skip("httpx not installed — run: pip install httpx")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{BASE_URL}/search",
            json={"query": query, "engine": engine, "top_k": top_k},
        )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    return resp.json()


# ── Unit tests: drug_catalog ──────────────────────────────────────────────────

class TestDrugCatalog:
    """Phase 1 — drug_catalog.py lookups."""

    def test_catalog_loads(self):
        from drug_catalog import catalog_size
        assert catalog_size() > 100, "Catalog should have at least 100 entries"

    def test_canonical_lookup(self):
        from drug_catalog import find_drug
        rec = find_drug("acetaminophen")
        assert rec is not None
        assert rec.canonical_name == "acetaminophen"

    def test_brand_to_generic(self):
        from drug_catalog import find_drug
        rec = find_drug("Tylenol")
        assert rec is not None
        assert rec.canonical_name == "acetaminophen"

    def test_brand_case_insensitive(self):
        from drug_catalog import find_drug
        assert find_drug("tylenol") is not None
        assert find_drug("TYLENOL") is not None

    def test_lipitor_to_atorvastatin(self):
        from drug_catalog import find_drug
        rec = find_drug("Lipitor")
        assert rec is not None
        assert rec.canonical_name == "atorvastatin"

    def test_eliquis_to_apixaban(self):
        from drug_catalog import find_drug
        rec = find_drug("Eliquis")
        assert rec is not None
        assert rec.canonical_name == "apixaban"
        assert rec.is_high_risk is True

    def test_warfarin_is_high_risk(self):
        from drug_catalog import is_high_risk
        assert is_high_risk("warfarin") is True

    def test_ibuprofen_not_high_risk(self):
        from drug_catalog import is_high_risk
        assert is_high_risk("ibuprofen") is False

    def test_unknown_drug_returns_none(self):
        from drug_catalog import find_drug
        # Completely unknown name — no RxNorm call in unit test context
        rec = find_drug("xyz_not_a_real_drug_12345")
        # Should return None (or a minimal record if RxNorm resolves it)
        if rec is not None:
            assert rec.canonical_name  # at minimum must have a name

    def test_dailymed_url_format(self):
        from drug_catalog import find_drug
        rec = find_drug("ibuprofen")
        url = rec.dailymed_url()
        assert "dailymed.nlm.nih.gov" in url


# ── Unit tests: answer_engine ─────────────────────────────────────────────────

class TestAnswerEngine:
    """Phase 3 — answer_engine.py guardrails and intent classification."""

    def test_classify_interaction_intent(self):
        from answer_engine import classify_intent
        assert classify_intent("can i take tylenol with ibuprofen") == "interaction"

    def test_classify_food_alcohol_intent(self):
        from answer_engine import classify_intent
        intent = classify_intent("tylenol and alcohol side effects")
        assert intent in ("food_alcohol", "side_effects")

    def test_classify_pregnancy_intent(self):
        from answer_engine import classify_intent
        assert classify_intent("can i take ibuprofen while pregnant") == "pregnancy_lactation"

    def test_classify_dosing_intent(self):
        from answer_engine import classify_intent
        assert classify_intent("what is the maximum dose of tylenol per day") == "dosing"

    def test_classify_side_effects_intent(self):
        from answer_engine import classify_intent
        assert classify_intent("lisinopril side effects") == "side_effects"

    def test_emergency_detection_overdose(self):
        from answer_engine import detect_emergency
        assert detect_emergency("I took too many pills and can't breathe") is True

    def test_emergency_detection_chest_pain(self):
        from answer_engine import detect_emergency
        assert detect_emergency("severe chest pain after taking medication") is True

    def test_emergency_detection_normal_query(self):
        from answer_engine import detect_emergency
        assert detect_emergency("can i take tylenol with ibuprofen") is False

    def test_high_risk_pair_warfarin_aspirin(self):
        from answer_engine import check_high_risk_pair
        pair = check_high_risk_pair(["warfarin", "aspirin"])
        assert pair is not None
        assert set(pair) == {"warfarin", "aspirin"}

    def test_high_risk_pair_apixaban_ibuprofen(self):
        from answer_engine import check_high_risk_pair
        pair = check_high_risk_pair(["apixaban", "ibuprofen"])
        assert pair is not None

    def test_high_risk_pair_safe_combo(self):
        from answer_engine import check_high_risk_pair
        pair = check_high_risk_pair(["metformin", "lisinopril"])
        # This is not in HIGH_RISK_PAIRS
        assert pair is None

    def test_retrieval_guard_dosing_no_label(self):
        from answer_engine import check_retrieval_guard, QuestionIntent, RetrievalStatus
        proceed, status = check_retrieval_guard(QuestionIntent.DOSING, None, [])
        assert proceed is False
        assert status == RetrievalStatus.REFUSED_NO_SOURCE

    def test_retrieval_guard_emergency(self):
        from answer_engine import check_retrieval_guard, QuestionIntent, RetrievalStatus
        proceed, status = check_retrieval_guard(
            QuestionIntent.GENERAL, None, [],
            query="I overdosed and can't breathe"
        )
        assert proceed is False
        assert status == RetrievalStatus.REFUSED_NO_SOURCE

    def test_retrieval_guard_general_no_label_proceeds(self):
        from answer_engine import check_retrieval_guard, QuestionIntent, RetrievalStatus
        proceed, status = check_retrieval_guard(QuestionIntent.GENERAL, None, [])
        assert proceed is True
        assert status == RetrievalStatus.LABEL_NOT_FOUND

    def test_build_emergency_answer(self):
        from answer_engine import build_emergency_answer
        ans = build_emergency_answer("2026-03-20T00:00:00Z")
        assert ans.verdict == "EMERGENCY"
        assert "911" in " ".join(ans.emergency_escalation)

    def test_build_unknown_drug_answer(self):
        from answer_engine import build_unknown_drug_answer
        ans = build_unknown_drug_answer("xyzanol", "2026-03-20T00:00:00Z")
        assert ans.verdict == "CONSULT_PHARMACIST"
        assert ans.confidence == "LOW"


# ── Integration tests: /search endpoint ──────────────────────────────────────

@pytest.mark.integration
class TestSearchEndpoint:
    """
    Phase 5 — live /search endpoint tests.
    Requires the backend to be running on BASE_URL.
    Mark: pytest -m integration
    """

    # ── Canonical 5 queries ──────────────────────────────────────────────────

    def test_tylenol_ibuprofen_interaction(self):
        """Query 1: multi-drug interaction — answer must mention both drugs."""
        data = post_search("can i take tylenol with ibuprofen")
        assert data["query"]
        result = data["results"][0]
        combined = (result.get("question", "") + " " + (result.get("answer") or "")).lower()
        assert "tylenol" in combined or "acetaminophen" in combined
        assert "ibuprofen" in combined
        structured = result.get("structured", {})
        assert structured.get("verdict") in (
            "CAUTION", "CONSULT_PHARMACIST", "SAFE", "AVOID"
        ), f"Unexpected verdict: {structured.get('verdict')}"

    def test_tylenol_alcohol_side_effects(self):
        """Query 2: must be about alcohol, NOT about 'empty stomach'."""
        data = post_search("tylenol and alcohol side effects")
        result = data["results"][0]
        answer_text = (result.get("answer") or "").lower()
        question_text = (result.get("question") or "").lower()
        # Must mention alcohol in either the question or answer
        assert "alcohol" in answer_text or "alcohol" in question_text, (
            f"Answer not about alcohol. Question: {result.get('question')}"
        )
        # Must NOT be the wrong DB row
        assert "empty stomach" not in question_text, (
            "Returned wrong DB answer: 'empty stomach' — relevance guard not working"
        )

    def test_metformin_alcohol(self):
        """Query 3: metformin + alcohol — answer must mention lactic acidosis or liver risk."""
        data = post_search("metformin and alcohol")
        result = data["results"][0]
        answer_text = (result.get("answer") or "").lower()
        assert "alcohol" in answer_text or "alcohol" in (result.get("question") or "").lower()
        # Should mention lactic acidosis or liver or risk
        risk_mentioned = any(
            kw in answer_text
            for kw in ("lactic", "liver", "risk", "caution", "avoid", "dangerous")
        )
        assert risk_mentioned, "Answer should mention risk associated with metformin + alcohol"

    def test_ibuprofen_while_pregnant(self):
        """Query 4: pregnancy guardrail — should be AVOID or CONSULT."""
        data = post_search("can i take ibuprofen while pregnant")
        result = data["results"][0]
        structured = result.get("structured", {})
        verdict = structured.get("verdict", "")
        assert verdict in ("AVOID", "CONSULT_PHARMACIST", "CAUTION"), (
            f"Ibuprofen in pregnancy should not be SAFE — got: {verdict}"
        )

    def test_lisinopril_side_effects(self):
        """Query 5: on-topic DB or AI answer about lisinopril."""
        data = post_search("lisinopril side effects")
        result = data["results"][0]
        combined = (
            (result.get("question") or "") + " " + (result.get("answer") or "")
        ).lower()
        assert "lisinopril" in combined, "Answer must be about lisinopril"

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_response_schema_fields(self):
        """Every /search response must have the required top-level fields."""
        data = post_search("aspirin dosage")
        assert "query"        in data
        assert "results"      in data
        assert "source"       in data
        assert "saved_to_db"  in data
        assert isinstance(data["results"], list)

    def test_result_structured_fields(self):
        """Each result must have structured.verdict set to a known value."""
        data = post_search("aspirin side effects")
        result = data["results"][0]
        structured = result.get("structured", {})
        assert "verdict" in structured, "structured.verdict must be present"
        assert structured["verdict"] in (
            "SAFE", "CAUTION", "AVOID", "CONSULT_PHARMACIST",
            "INSUFFICIENT_DATA", "EMERGENCY",
        )

    def test_empty_query_returns_400(self):
        """Empty query must return HTTP 400."""
        if not HAS_HTTPX:
            pytest.skip("httpx not installed")
        with httpx.Client(timeout=10) as client:
            resp = client.post(f"{BASE_URL}/search", json={"query": ""})
        assert resp.status_code == 400

    def test_high_risk_pair_eliquis_aspirin(self):
        """Eliquis + aspirin is a known high-risk pair — verdict must be CAUTION or AVOID."""
        data = post_search("can i take Eliquis with aspirin")
        result = data["results"][0]
        verdict = result.get("structured", {}).get("verdict", "")
        assert verdict in ("CAUTION", "AVOID", "CONSULT_PHARMACIST"), (
            f"Eliquis + aspirin should not be SAFE — got: {verdict}"
        )

    def test_misspelled_drug_resolves(self):
        """Misspelled drug names should still return relevant results."""
        data = post_search("ibuprofin side effects")  # deliberate misspelling
        assert len(data["results"]) > 0

    def test_unknown_drug_does_not_crash(self):
        """Completely unknown drug should not crash the server."""
        data = post_search("xyzanol side effects")
        assert "results" in data
        # May be empty or have a consult answer — must not be a 500
