"""
Pipeline v2 — Deterministic Test Suite.

Tests the 5 required scenarios + additional edge cases.
All tests run WITHOUT network calls — they use the decision engine's
deterministic pair table and classifier directly.

Required test cases:
  1. ibuprofen + warfarin     → AVOID
  2. metformin + ibuprofen    → CAUTION
  3. side effects metformin   → CAUTION
  4. ibuprofen dosage         → CONSULT_PHARMACIST
  5. amoxicillin + food       → SAFE

Run: python -m pytest backend/tests/test_pipeline.py -v
  or: cd backend && python tests/test_pipeline.py
"""
from __future__ import annotations

import os
import sys

# Ensure backend/ is on the import path
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from pipeline.classifier import Intent, classify_fast, is_emergency
from pipeline.drug_extractor import extract_drug_names, normalize_query
from pipeline.decision_engine import (
    compute_verdict,
    DecisionResult,
    AVOID, CAUTION, SAFE, CONSULT_PHARMACIST, EMERGENCY,
    DETERMINISTIC_PAIRS,
)
from pipeline.api_layer import APIResults
from pipeline.verdict_enforcer import enforce_verdict
from pipeline.response_cleaner import clean_response
from pipeline.claude_explainer import Explanation, _build_fallback
from pipeline.failsafe import build_failsafe_response, build_emergency_response


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFIER TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_classifier_interaction():
    """2-drug query with 'with' → INTERACTION"""
    assert classify_fast("can i take ibuprofen with warfarin", drug_count=2) == Intent.INTERACTION

def test_classifier_dosing():
    """Dosage keywords → DOSING"""
    assert classify_fast("ibuprofen dosage", drug_count=1) == Intent.DOSING

def test_classifier_side_effects():
    """Side effects keywords + 1 drug → SIDE_EFFECTS"""
    assert classify_fast("side effects of metformin", drug_count=1) == Intent.SIDE_EFFECTS

def test_classifier_food():
    """Food keywords → FOOD_ALCOHOL"""
    assert classify_fast("can I take amoxicillin with food", drug_count=1) == Intent.FOOD_ALCOHOL

def test_classifier_what_is():
    """What is → WHAT_IS"""
    assert classify_fast("what is metformin used for", drug_count=1) == Intent.WHAT_IS

def test_classifier_pregnancy():
    """Pregnancy keywords → PREGNANCY_LACTATION"""
    assert classify_fast("is sertraline safe during pregnancy", drug_count=1) == Intent.PREGNANCY_LACTATION

def test_classifier_side_effects_beats_interaction_1_drug():
    """Side effects wins over interaction when only 1 drug present."""
    result = classify_fast("duloxetine side effects", drug_count=1)
    assert result == Intent.SIDE_EFFECTS

def test_classifier_emergency():
    """Emergency keywords detected."""
    assert is_emergency("I overdosed on tylenol")
    assert is_emergency("can't breathe after taking pills")
    assert not is_emergency("what is ibuprofen used for")


# ══════════════════════════════════════════════════════════════════════════════
# DRUG EXTRACTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_extract_single_drug():
    drugs = extract_drug_names("what are the side effects of ibuprofen")
    assert "ibuprofen" in drugs

def test_extract_two_drugs():
    drugs = extract_drug_names("can I take ibuprofen with warfarin")
    assert "ibuprofen" in drugs
    assert "warfarin" in drugs

def test_extract_brand_name():
    drugs = extract_drug_names("can I take Tylenol and Advil together")
    assert "acetaminophen" in drugs
    assert "ibuprofen" in drugs

def test_normalize_query_slang():
    original, cleaned = normalize_query("yo can I take xanny with booze")
    assert "xanax" in cleaned
    assert "alcohol" in cleaned

def test_normalize_query_misspelling():
    original, cleaned = normalize_query("ibuprofin dosage for adults")
    assert "ibuprofen" in cleaned


# ══════════════════════════════════════════════════════════════════════════════
# DECISION ENGINE TESTS — THE 5 REQUIRED SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

def _empty_api() -> APIResults:
    """Create empty API results (no network calls)."""
    return APIResults()


def test_required_1_ibuprofen_warfarin_avoid():
    """
    REQUIRED TEST 1: ibuprofen + warfarin → AVOID
    This is a deterministic pair — no API calls needed.
    """
    result = compute_verdict(
        intent=Intent.INTERACTION,
        drug_names=["ibuprofen", "warfarin"],
        api_results=_empty_api(),
        query="can I take ibuprofen with warfarin",
    )
    assert result.verdict == AVOID, f"Expected AVOID, got {result.verdict}"
    assert result.confidence == "HIGH"
    print(f"✓ TEST 1 PASS: ibuprofen + warfarin → {result.verdict}")


def test_required_2_metformin_ibuprofen_caution():
    """
    REQUIRED TEST 2: metformin + ibuprofen → CAUTION
    This is a deterministic pair — kidney strain / lactic acidosis risk.
    """
    result = compute_verdict(
        intent=Intent.INTERACTION,
        drug_names=["metformin", "ibuprofen"],
        api_results=_empty_api(),
        query="can I take metformin with ibuprofen",
    )
    assert result.verdict == CAUTION, f"Expected CAUTION, got {result.verdict}"
    print(f"✓ TEST 2 PASS: metformin + ibuprofen → {result.verdict}")


def test_required_3_side_effects_metformin_caution():
    """
    REQUIRED TEST 3: side effects metformin → CAUTION
    Side effects intent always returns CAUTION.
    """
    result = compute_verdict(
        intent=Intent.SIDE_EFFECTS,
        drug_names=["metformin"],
        api_results=_empty_api(),
        query="side effects of metformin",
    )
    assert result.verdict == CAUTION, f"Expected CAUTION, got {result.verdict}"
    print(f"✓ TEST 3 PASS: side effects metformin → {result.verdict}")


def test_required_4_ibuprofen_dosage_consult():
    """
    REQUIRED TEST 4: ibuprofen dosage → CONSULT_PHARMACIST
    Dosing intent always returns CONSULT_PHARMACIST.
    """
    result = compute_verdict(
        intent=Intent.DOSING,
        drug_names=["ibuprofen"],
        api_results=_empty_api(),
        query="ibuprofen dosage for adults",
    )
    assert result.verdict == CONSULT_PHARMACIST, f"Expected CONSULT_PHARMACIST, got {result.verdict}"
    print(f"✓ TEST 4 PASS: ibuprofen dosage → {result.verdict}")


def test_required_5_amoxicillin_food_safe():
    """
    REQUIRED TEST 5: amoxicillin + food → SAFE
    Food intent with no flagged interaction → SAFE.
    """
    result = compute_verdict(
        intent=Intent.FOOD_ALCOHOL,
        drug_names=["amoxicillin"],
        api_results=_empty_api(),
        query="can I take amoxicillin with food",
    )
    assert result.verdict == SAFE, f"Expected SAFE, got {result.verdict}"
    print(f"✓ TEST 5 PASS: amoxicillin + food → {result.verdict}")


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL DECISION ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_sildenafil_nitroglycerin_avoid():
    """PDE-5 + nitrate → AVOID (fatal hypotension)."""
    result = compute_verdict(
        intent=Intent.INTERACTION,
        drug_names=["sildenafil", "nitroglycerin"],
        api_results=_empty_api(),
        query="can I take viagra with nitroglycerin",
    )
    assert result.verdict == AVOID

def test_sertraline_tramadol_avoid():
    """SSRI + tramadol → AVOID (serotonin syndrome)."""
    result = compute_verdict(
        intent=Intent.INTERACTION,
        drug_names=["sertraline", "tramadol"],
        api_results=_empty_api(),
        query="can I take sertraline with tramadol",
    )
    assert result.verdict == AVOID

def test_acetaminophen_alcohol_caution():
    """Acetaminophen + alcohol → CAUTION."""
    result = compute_verdict(
        intent=Intent.FOOD_ALCOHOL,
        drug_names=["acetaminophen"],
        api_results=_empty_api(),
        query="can I drink alcohol with tylenol",
    )
    assert result.verdict == CAUTION

def test_what_is_safe():
    """What is X → SAFE (informational)."""
    result = compute_verdict(
        intent=Intent.WHAT_IS,
        drug_names=["metformin"],
        api_results=_empty_api(),
        query="what is metformin used for",
    )
    assert result.verdict == SAFE

def test_dosing_always_consult():
    """Any dosing question → CONSULT_PHARMACIST regardless of drug."""
    for drug in ["metformin", "warfarin", "ibuprofen", "sertraline"]:
        result = compute_verdict(
            intent=Intent.DOSING,
            drug_names=[drug],
            api_results=_empty_api(),
            query=f"what is the dosage for {drug}",
        )
        assert result.verdict == CONSULT_PHARMACIST, f"{drug} dosing should be CONSULT_PHARMACIST"

def test_contraindications_without_label_consult():
    """Contraindications without FDA label → CONSULT_PHARMACIST."""
    result = compute_verdict(
        intent=Intent.CONTRAINDICATIONS,
        drug_names=["some_unknown_drug"],
        api_results=_empty_api(),
        query="contraindications for some_unknown_drug",
    )
    assert result.verdict == CONSULT_PHARMACIST

def test_pregnancy_without_label_consult():
    """Pregnancy without FDA label → CONSULT_PHARMACIST."""
    result = compute_verdict(
        intent=Intent.PREGNANCY_LACTATION,
        drug_names=["some_unknown_drug"],
        api_results=_empty_api(),
        query="is some_unknown_drug safe during pregnancy",
    )
    assert result.verdict == CONSULT_PHARMACIST


# ══════════════════════════════════════════════════════════════════════════════
# VERDICT ENFORCER TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_enforcer_strips_contradiction():
    """If verdict is SAFE but Claude says 'avoid', rewrite the answer."""
    explanation = Explanation(
        answer="You should avoid taking these together.",
        warning="Do not combine.",
    )
    enforced = enforce_verdict("SAFE", explanation, ["ibuprofen"], "general")
    assert "avoid" not in enforced.answer.lower()

def test_enforcer_adds_warning_for_avoid():
    """AVOID verdict must have a warning."""
    explanation = Explanation(answer="Some answer.", warning="")
    enforced = enforce_verdict("AVOID", explanation, ["ibuprofen", "warfarin"], "interaction")
    assert enforced.warning != ""


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE CLEANER TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_cleaner_strips_markdown():
    """Markdown formatting removed."""
    explanation = Explanation(answer="**Bold text** and *italic*.", warning="")
    cleaned = clean_response(explanation)
    assert "**" not in cleaned.answer
    assert "*" not in cleaned.answer

def test_cleaner_limits_sentences():
    """Max 2 sentences in answer."""
    explanation = Explanation(
        answer="Sentence one. Sentence two. Sentence three. Sentence four.",
        warning="",
    )
    cleaned = clean_response(explanation)
    # Count sentences
    import re
    sentences = re.split(r"(?<=[.!?])\s+", cleaned.answer)
    assert len(sentences) <= 2


# ══════════════════════════════════════════════════════════════════════════════
# FAILSAFE TESTS
# ══════════════════════════════════════════════════════════════════════════════

def test_failsafe_response():
    """Failsafe always returns CONSULT_PHARMACIST."""
    result = build_failsafe_response("broken query", "API failure")
    assert result["results"][0]["structured"]["verdict"] == "CONSULT_PHARMACIST"

def test_emergency_response():
    """Emergency always returns EMERGENCY."""
    result = build_emergency_response("I overdosed on pills")
    assert result["results"][0]["structured"]["verdict"] == "EMERGENCY"


# ══════════════════════════════════════════════════════════════════════════════
# DETERMINISTIC PAIR TABLE COVERAGE TEST
# ══════════════════════════════════════════════════════════════════════════════

def test_all_deterministic_pairs_have_verdicts():
    """Every deterministic pair must map to AVOID or CAUTION."""
    for pair, (verdict, reason) in DETERMINISTIC_PAIRS.items():
        assert verdict in (AVOID, CAUTION), f"Pair {pair} has invalid verdict: {verdict}"
        assert len(reason) > 10, f"Pair {pair} has too-short reason: {reason}"
    print(f"✓ All {len(DETERMINISTIC_PAIRS)} deterministic pairs validated")


# ══════════════════════════════════════════════════════════════════════════════
# END-TO-END INTEGRATION (without network)
# ══════════════════════════════════════════════════════════════════════════════

def test_full_pipeline_no_network():
    """
    Run the full classify → decide → explain → enforce → clean pipeline
    without any network calls. Verifies the complete chain works.
    """
    query = "can I take ibuprofen with warfarin"

    # Step 1: Classify
    drugs = extract_drug_names(query)
    intent = classify_fast(query, drug_count=len(drugs))
    assert intent == Intent.INTERACTION
    assert "ibuprofen" in drugs
    assert "warfarin" in drugs

    # Step 5: Decision
    decision = compute_verdict(intent, drugs, _empty_api(), query)
    assert decision.verdict == AVOID

    # Step 6: Explanation (fallback — no Claude)
    explanation = _build_fallback(decision.verdict, decision.reasoning, drugs, intent.value)
    assert explanation.answer != ""

    # Step 7: Enforce
    enforced = enforce_verdict(decision.verdict, explanation, drugs, intent.value)
    assert "avoid" in enforced.answer.lower() or "do not" in enforced.answer.lower()

    # Step 8: Clean
    cleaned = clean_response(enforced)
    assert "**" not in cleaned.answer

    print(f"✓ Full pipeline: '{query}' → verdict={decision.verdict}")


# ══════════════════════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    errors = []

    print(f"\n{'='*60}")
    print(f"RxBuddy Pipeline v2 — Test Suite ({len(tests)} tests)")
    print(f"{'='*60}\n")

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
            print(f"  ✓ {test_fn.__name__}")
        except AssertionError as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            print(f"  ✗ {test_fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            print(f"  ✗ {test_fn.__name__}: EXCEPTION: {e}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'='*60}")

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")

    sys.exit(0 if failed == 0 else 1)
