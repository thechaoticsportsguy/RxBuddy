"""
Tests for the intent classifier.

All 10 required cases from the spec must pass.  Run with:
    cd backend && python -m pytest tests/test_intent_classifier.py -v
or:
    cd backend && python tests/test_intent_classifier.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from answer_engine import classify_intent, QuestionIntent


def test_duloxetine_side_effects_is_side_effects():
    """1. One drug + side effects keyword → SIDE_EFFECTS (never INTERACTION)."""
    result = classify_intent("duloxetine side effects", drug_count=1)
    assert result == QuestionIntent.SIDE_EFFECTS, (
        f"Expected SIDE_EFFECTS, got {result}. "
        "This is the core bug: 1-drug side-effects query must not route to INTERACTION."
    )


def test_side_effects_of_ibuprofen_is_side_effects():
    """2. 'side effects of X' phrasing → SIDE_EFFECTS."""
    result = classify_intent("side effects of ibuprofen", drug_count=1)
    assert result == QuestionIntent.SIDE_EFFECTS, f"Expected SIDE_EFFECTS, got {result}"


def test_what_does_metformin_do_is_what_is():
    """3. 'what does X do' → WHAT_IS."""
    result = classify_intent("what does metformin do", drug_count=1)
    assert result == QuestionIntent.WHAT_IS, f"Expected WHAT_IS, got {result}"


def test_what_is_lisinopril_for_is_what_is():
    """4. 'what is X for' → WHAT_IS."""
    result = classify_intent("what is lisinopril for", drug_count=1)
    assert result == QuestionIntent.WHAT_IS, f"Expected WHAT_IS, got {result}"


def test_can_i_take_duloxetine_with_phenelzine_is_interaction():
    """5. Two drugs + 'with' → INTERACTION."""
    result = classify_intent("can i take duloxetine with phenelzine", drug_count=2)
    assert result == QuestionIntent.INTERACTION, f"Expected INTERACTION, got {result}"


def test_duloxetine_and_alcohol_is_food_alcohol_or_interaction():
    """6. Drug + alcohol → FOOD_ALCOHOL (or INTERACTION — both are acceptable)."""
    result = classify_intent("duloxetine and alcohol", drug_count=1)
    assert result in (QuestionIntent.FOOD_ALCOHOL, QuestionIntent.INTERACTION), (
        f"Expected FOOD_ALCOHOL or INTERACTION, got {result}"
    )


def test_metformin_dosing_is_dosing():
    """7. 'X dosing' → DOSING."""
    result = classify_intent("metformin dosing", drug_count=1)
    assert result == QuestionIntent.DOSING, f"Expected DOSING, got {result}"


def test_how_much_tylenol_per_day_is_dosing():
    """8. 'how much X' → DOSING."""
    result = classify_intent("how much tylenol per day", drug_count=1)
    assert result == QuestionIntent.DOSING, f"Expected DOSING, got {result}"


def test_is_ibuprofen_safe_while_pregnant_is_safety_or_pregnancy():
    """9. Safety + pregnancy context → SAFETY or PREGNANCY_LACTATION (both acceptable)."""
    result = classify_intent("is ibuprofen safe while pregnant", drug_count=1)
    assert result in (QuestionIntent.SAFETY, QuestionIntent.PREGNANCY_LACTATION), (
        f"Expected SAFETY or PREGNANCY_LACTATION, got {result}"
    )


def test_warfarin_contraindications_is_contraindications():
    """10. 'X contraindications' → CONTRAINDICATIONS."""
    result = classify_intent("warfarin contraindications", drug_count=1)
    assert result == QuestionIntent.CONTRAINDICATIONS, f"Expected CONTRAINDICATIONS, got {result}"


# ── Regression: SIDE_EFFECTS beats strong interaction keywords with 1 drug ───

def test_side_effects_beats_interact_keyword_single_drug():
    """SIDE_EFFECTS must win over strong interaction kw when only 1 drug present."""
    # "interact" is in _INTERACTION_STRONG_KW but there's only 1 drug
    result = classify_intent("duloxetine side effects and interactions", drug_count=1)
    assert result == QuestionIntent.SIDE_EFFECTS, (
        f"Expected SIDE_EFFECTS (1 drug), got {result}"
    )


def test_two_drugs_side_effects_may_be_interaction():
    """With 2 drugs, side effects keyword does NOT necessarily force SIDE_EFFECTS."""
    result = classify_intent("side effects of duloxetine and phenelzine together", drug_count=2)
    # "together" is a strong interaction keyword → INTERACTION expected
    assert result == QuestionIntent.INTERACTION, (
        f"Expected INTERACTION (2 drugs + 'together'), got {result}"
    )


if __name__ == "__main__":
    tests = [
        test_duloxetine_side_effects_is_side_effects,
        test_side_effects_of_ibuprofen_is_side_effects,
        test_what_does_metformin_do_is_what_is,
        test_what_is_lisinopril_for_is_what_is,
        test_can_i_take_duloxetine_with_phenelzine_is_interaction,
        test_duloxetine_and_alcohol_is_food_alcohol_or_interaction,
        test_metformin_dosing_is_dosing,
        test_how_much_tylenol_per_day_is_dosing,
        test_is_ibuprofen_safe_while_pregnant_is_safety_or_pregnancy,
        test_warfarin_contraindications_is_contraindications,
        test_side_effects_beats_interact_keyword_single_drug,
        test_two_drugs_side_effects_may_be_interaction,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
    if failed == 0:
        pass  # all passed
    else:
        sys.exit(1)
