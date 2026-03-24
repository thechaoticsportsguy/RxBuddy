"""
Pipeline Step 5 — Deterministic Backend Decision Engine (PRIMARY TRUTH).

This is the SINGLE SOURCE OF TRUTH for all verdicts. Claude NEVER decides
the verdict — only this module does.

Verdict hierarchy: AVOID > CAUTION > SAFE > CONSULT_PHARMACIST

Rules by intent:
  DOSING               → always CONSULT_PHARMACIST (we don't give dosing advice)
  SIDE_EFFECTS         → always CAUTION (side effects exist for every drug)
  CONTRAINDICATIONS    → CONSULT_PHARMACIST if no label, else CAUTION/AVOID
  PREGNANCY_LACTATION  → CONSULT_PHARMACIST if no label, else CAUTION/AVOID
  INTERACTION          → compute_interaction_verdict() — pair-by-pair analysis
  FOOD_ALCOHOL         → default SAFE unless API flags issue
  WHAT_IS              → SAFE (informational)
  SAFETY               → determined by API signals
  GENERAL              → SAFE (informational)

Multi-drug interaction rules:
  - Evaluate ALL unique pairs
  - If ANY pair is AVOID → overall AVOID
  - If ANY pair is CAUTION → overall CAUTION
  - If ALL pairs are SAFE → SAFE
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pipeline.api_layer import APIResults
from pipeline.classifier import Intent

logger = logging.getLogger("rxbuddy.pipeline.decision_engine")


# ── Verdict enum ──────────────────────────────────────────────────────────────
# Using plain strings for simplicity (matches the frontend API contract)
AVOID               = "AVOID"
CAUTION             = "CAUTION"
SAFE                = "SAFE"
CONSULT_PHARMACIST  = "CONSULT_PHARMACIST"
INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"
EMERGENCY           = "EMERGENCY"


# ── Result container ──────────────────────────────────────────────────────────
@dataclass
class DecisionResult:
    """Output from the decision engine."""
    verdict: str                               # SAFE | CAUTION | AVOID | CONSULT_PHARMACIST | EMERGENCY
    confidence: str = "HIGH"                   # HIGH | MEDIUM | LOW
    reasoning: str = ""                        # Brief internal reason (for logging / Claude context)
    interaction_pairs: dict = field(default_factory=lambda: {"avoid_pairs": [], "caution_pairs": []})
    retrieval_status: str = "LABEL_NOT_FOUND"  # LABEL_FOUND | LABEL_NOT_FOUND | REFUSED_NO_SOURCE
    is_deterministic: bool = False             # True if resolved by known-pair table (no Claude needed)


# ── Deterministic pair verdicts ───────────────────────────────────────────────
# Pre-resolved answers for the highest-stakes drug combinations.
# frozenset({drug_a, drug_b}) → (VERDICT, one-sentence reason)
# These SHORT-CIRCUIT Claude — no API call, no hallucination risk.
DETERMINISTIC_PAIRS: dict[frozenset, tuple[str, str]] = {
    # Anticoagulant + NSAID → major bleeding
    frozenset({"warfarin", "aspirin"}):     (AVOID, "Warfarin + aspirin significantly increases bleeding risk."),
    frozenset({"warfarin", "ibuprofen"}):   (AVOID, "NSAIDs substantially raise bleeding risk with warfarin."),
    frozenset({"warfarin", "naproxen"}):    (AVOID, "Naproxen + warfarin significantly increases serious bleeding risk."),
    frozenset({"warfarin", "diclofenac"}):  (AVOID, "Diclofenac + warfarin raises bleeding risk and destabilises anticoagulation."),
    frozenset({"apixaban", "ibuprofen"}):   (AVOID, "NSAIDs increase bleeding risk with apixaban (Eliquis)."),
    frozenset({"apixaban", "aspirin"}):     (CAUTION, "Dual use significantly raises bleeding risk; only if doctor prescribed both."),
    frozenset({"rivaroxaban", "ibuprofen"}):(AVOID, "NSAIDs increase bleeding risk with rivaroxaban (Xarelto)."),
    frozenset({"rivaroxaban", "aspirin"}):  (CAUTION, "Combining raises bleeding risk; only under medical supervision."),
    frozenset({"dabigatran", "ibuprofen"}): (AVOID, "This combination significantly increases gastrointestinal bleeding risk."),
    # Nitrate + PDE-5 → severe hypotension / fatal
    frozenset({"sildenafil", "nitroglycerin"}):          (AVOID, "Combination can cause a life-threatening drop in blood pressure."),
    frozenset({"tadalafil", "nitroglycerin"}):           (AVOID, "Combination can cause dangerous, potentially fatal hypotension."),
    frozenset({"sildenafil", "isosorbide mononitrate"}): (AVOID, "Combination causes severe, potentially fatal hypotension."),
    # Serotonin syndrome
    frozenset({"sertraline", "tramadol"}):  (AVOID, "This combination can cause serotonin syndrome, a potentially life-threatening condition."),
    frozenset({"fluoxetine", "tramadol"}):  (AVOID, "Combination raises serotonin syndrome risk, which can be life-threatening."),
    frozenset({"linezolid", "sertraline"}): (AVOID, "Linezolid has MAOI properties that can cause fatal serotonin syndrome with sertraline."),
    frozenset({"linezolid", "fluoxetine"}): (AVOID, "Linezolid + fluoxetine can cause fatal serotonin syndrome."),
    # Narrow-TI drug toxicity
    frozenset({"lithium", "ibuprofen"}):    (AVOID, "NSAIDs reduce lithium excretion and can push levels into toxic range."),
    frozenset({"lithium", "naproxen"}):     (AVOID, "NSAIDs impair lithium clearance and can cause lithium toxicity."),
    frozenset({"digoxin", "amiodarone"}):   (AVOID, "Amiodarone doubles digoxin levels, risking digoxin toxicity."),
    frozenset({"digoxin", "verapamil"}):    (AVOID, "Verapamil raises digoxin levels and increases toxicity risk."),
    frozenset({"methotrexate", "ibuprofen"}):  (AVOID, "NSAIDs reduce methotrexate clearance and can cause serious toxicity."),
    frozenset({"methotrexate", "naproxen"}):   (AVOID, "This combination can cause life-threatening methotrexate toxicity."),
    # Retinoid + tetracycline → pseudotumor cerebri
    frozenset({"isotretinoin", "doxycycline"}): (AVOID, "Both raise intracranial pressure; combination risks pseudotumor cerebri."),
    frozenset({"isotretinoin", "minocycline"}): (AVOID, "Combination significantly increases risk of raised intracranial pressure."),
    # Kidney / lactic acidosis risk
    frozenset({"metformin", "ibuprofen"}):   (CAUTION, "Ibuprofen can impair kidneys, reducing metformin clearance and raising lactic acidosis risk."),
    frozenset({"metformin", "naproxen"}):    (CAUTION, "Naproxen can impair kidneys, reducing metformin clearance."),
    frozenset({"lisinopril", "ibuprofen"}):  (CAUTION, "Ibuprofen reduces lisinopril's blood-pressure effect and together can cause acute kidney injury."),
    frozenset({"lisinopril", "naproxen"}):   (CAUTION, "Naproxen reduces lisinopril's blood-pressure effect; combined AKI risk."),
    frozenset({"lisinopril", "potassium chloride"}): (CAUTION, "Lisinopril already raises potassium; adding supplements risks hyperkalemia."),
    frozenset({"lisinopril", "spironolactone"}):     (CAUTION, "Both raise potassium; combination risks hyperkalemia."),
}

# Acetaminophen + alcohol special case
_ACETA_TERMS   = frozenset({"acetaminophen", "tylenol", "paracetamol"})
_ALCOHOL_TERMS = frozenset({"alcohol", "drinking", "drink", "beer", "wine"})


# ── Drug class sets (for heuristic pair evaluation) ───────────────────────────
NSAID_DRUGS          = frozenset({"ibuprofen", "naproxen", "aspirin", "celecoxib", "meloxicam", "diclofenac", "indomethacin", "ketorolac"})
ANTICOAGULANT_DRUGS  = frozenset({"warfarin", "heparin", "enoxaparin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban"})
ANTIPLATELET_DRUGS   = frozenset({"clopidogrel", "prasugrel", "ticagrelor", "aspirin"})
SSRI_DRUGS           = frozenset({"sertraline", "fluoxetine", "escitalopram", "paroxetine", "citalopram", "fluvoxamine"})
OPIOID_DRUGS         = frozenset({"tramadol", "hydrocodone", "oxycodone", "morphine", "fentanyl", "codeine", "methadone", "buprenorphine"})
MAOI_DRUGS           = frozenset({"linezolid", "phenelzine", "tranylcypromine", "selegiline", "isocarboxazid"})
NARROW_TI_DRUGS      = frozenset({"warfarin", "lithium", "digoxin", "phenytoin", "theophylline", "vancomycin", "methotrexate", "cyclosporine", "tacrolimus"})

# ── Interaction signal keywords (found in FDA label text) ─────────────────────
AVOID_SIGNALS = (
    "contraindicated", "do not use", "do not take", "should not be used",
    "life-threatening", "fatal", "potentially fatal", "severe hypotension",
    "serotonin syndrome", "major bleeding", "hemorrhage", "toxic",
    "black box", "boxed warning",
)
CAUTION_SIGNALS = (
    "monitor", "monitoring", "use with caution", "dose adjustment",
    "increased risk", "may increase", "may reduce", "may decrease",
    "caution is advised", "moderate interaction", "close supervision",
    "reduced clearance", "elevated levels",
)
SAFE_SIGNALS = (
    "no known interaction", "no significant interaction", "no clinically significant",
    "safe to take together", "no major interaction", "compatible",
    "low interaction risk",
)


# ── Core verdict computation ──────────────────────────────────────────────────

def _check_deterministic_pair(drug_a: str, drug_b: str) -> tuple[str, str] | None:
    """
    Check if this drug pair has a pre-resolved deterministic verdict.
    Returns (verdict, reason) or None.
    """
    pair = frozenset({drug_a.lower(), drug_b.lower()})
    return DETERMINISTIC_PAIRS.get(pair)


def _evaluate_pair_from_api(
    drug_a: str,
    drug_b: str,
    api_results: APIResults,
) -> tuple[str, str]:
    """
    Evaluate a drug pair using API data (FDA labels + RxNav interactions).
    Returns (verdict, reason).
    """
    a_lower, b_lower = drug_a.lower(), drug_b.lower()

    # 1. Check RxNav vetted interactions (highest signal quality)
    for ixn in api_results.rxnav_interactions:
        severity = (ixn.get("severity") or "").upper()
        if severity in ("HIGH", "MAJOR"):
            return AVOID, ixn.get("description", "Major interaction found by DrugBank.")[:150]
        if severity in ("MODERATE",):
            return CAUTION, ixn.get("description", "Moderate interaction found by DrugBank.")[:150]

    # 2. Cross-reference FDA labels: does drug A's interaction section mention drug B?
    for source_drug, target_drug in [(a_lower, b_lower), (b_lower, a_lower)]:
        fda = api_results.fda_labels.get(source_drug, {})
        ixn_text = (fda.get("drug_interactions") or "").lower()
        if not ixn_text:
            continue

        if target_drug in ixn_text:
            # Found a cross-reference — check severity signals
            if any(sig in ixn_text for sig in AVOID_SIGNALS):
                return AVOID, f"FDA label for {source_drug} mentions {target_drug} with serious risk signals."
            if any(sig in ixn_text for sig in CAUTION_SIGNALS):
                return CAUTION, f"FDA label for {source_drug} mentions {target_drug} with caution signals."
            return CAUTION, f"FDA label for {source_drug} mentions {target_drug} in drug interactions section."

    # 3. Drug class heuristics (no API data needed)
    pair_set = {a_lower, b_lower}
    if pair_set & NSAID_DRUGS and pair_set & ANTICOAGULANT_DRUGS:
        return AVOID, "NSAID + anticoagulant combination increases bleeding risk."
    if pair_set & NSAID_DRUGS and pair_set & ANTIPLATELET_DRUGS:
        return CAUTION, "NSAID + antiplatelet may increase bleeding risk."
    if pair_set & SSRI_DRUGS and pair_set & OPIOID_DRUGS:
        return CAUTION, "SSRI + opioid combination raises serotonin syndrome risk."
    if pair_set & SSRI_DRUGS and pair_set & MAOI_DRUGS:
        return AVOID, "SSRI + MAOI combination can cause fatal serotonin syndrome."

    # 4. No signals found
    if any(d in NARROW_TI_DRUGS for d in pair_set):
        return CAUTION, "One drug has a narrow therapeutic index; monitor closely."

    return SAFE, "No known interaction found between these drugs."


def _evaluate_all_pairs(
    drug_names: list[str],
    api_results: APIResults,
) -> tuple[str, str, dict]:
    """
    Evaluate ALL unique drug pairs and return the aggregate verdict.

    Returns (verdict, reasoning, interaction_summary).
    """
    if len(drug_names) < 2:
        return CONSULT_PHARMACIST, "Single drug — cannot evaluate interactions.", {"avoid_pairs": [], "caution_pairs": []}

    summary = {"avoid_pairs": [], "caution_pairs": []}
    worst_verdict = SAFE
    worst_reason = "No known interactions found."

    unique = list(dict.fromkeys(d.lower() for d in drug_names))
    for i, left in enumerate(unique):
        for right in unique[i + 1:]:
            # 1. Check deterministic table first
            det = _check_deterministic_pair(left, right)
            if det:
                verdict, reason = det
            else:
                # 2. Check API data
                verdict, reason = _evaluate_pair_from_api(left, right, api_results)

            label = f"{left} + {right}"
            if verdict == AVOID:
                summary["avoid_pairs"].append(label)
                worst_verdict = AVOID
                worst_reason = reason
            elif verdict == CAUTION:
                summary["caution_pairs"].append(label)
                if worst_verdict != AVOID:
                    worst_verdict = CAUTION
                    worst_reason = reason

    return worst_verdict, worst_reason, summary


def _check_acetaminophen_alcohol(drug_names: list[str], query: str) -> tuple[str, str] | None:
    """Special case: acetaminophen + alcohol."""
    names_set = {d.lower() for d in drug_names}
    q_lower = query.lower()
    has_aceta = bool(names_set & _ACETA_TERMS) or any(t in q_lower for t in _ACETA_TERMS)
    has_alcohol = bool(names_set & _ALCOHOL_TERMS) or any(t in q_lower for t in _ALCOHOL_TERMS)
    if has_aceta and has_alcohol:
        return CAUTION, "Regular or heavy alcohol use with acetaminophen significantly increases liver damage risk."
    return None


# ── Main decision function ────────────────────────────────────────────────────

def compute_verdict(
    intent: Intent | str,
    drug_names: list[str],
    api_results: APIResults,
    query: str = "",
) -> DecisionResult:
    """
    THE deterministic backend decision engine. PRIMARY TRUTH.

    Claude NEVER overrides this. The verdict from this function is final.

    Parameters
    ----------
    intent      : classified query intent
    drug_names  : list of normalised generic drug names
    api_results : all API data fetched in parallel
    query       : original user query (for special-case detection)

    Returns
    -------
    DecisionResult with the final verdict, confidence, and reasoning.
    """
    intent_str = intent.value if hasattr(intent, "value") else str(intent)
    has_any_label = bool(api_results.fda_labels)
    primary_drug = drug_names[0] if drug_names else ""
    has_primary_label = primary_drug in api_results.fda_labels

    result = DecisionResult(verdict=SAFE)

    # ── DOSING: always CONSULT_PHARMACIST ─────────────────────────────────
    # We never provide specific dosing advice — too dangerous.
    if intent_str == "dosing":
        result.verdict = CONSULT_PHARMACIST
        result.confidence = "HIGH"
        result.reasoning = "Dosing questions require pharmacist guidance — we do not provide specific dose recommendations."
        if not has_primary_label:
            result.retrieval_status = "REFUSED_NO_SOURCE"
        else:
            result.retrieval_status = "LABEL_FOUND"
        return result

    # ── SIDE_EFFECTS: always CAUTION ──────────────────────────────────────
    # Every drug has side effects. Answering "SAFE" for side effects is wrong.
    if intent_str == "side_effects":
        result.verdict = CAUTION
        result.confidence = "HIGH" if has_primary_label else "MEDIUM"
        result.reasoning = "All medications have potential side effects; always use with awareness."
        result.retrieval_status = "LABEL_FOUND" if has_primary_label else "LABEL_NOT_FOUND"
        return result

    # ── CONTRAINDICATIONS / PREGNANCY: refuse without label ───────────────
    if intent_str in ("contraindications", "pregnancy_lactation"):
        if not has_primary_label:
            result.verdict = CONSULT_PHARMACIST
            result.confidence = "LOW"
            result.reasoning = "Cannot answer contraindication/pregnancy questions without verified FDA label data."
            result.retrieval_status = "REFUSED_NO_SOURCE"
            return result
        # With label: check for boxed warnings
        fda = api_results.fda_labels.get(primary_drug, {})
        if fda.get("boxed_warning"):
            result.verdict = AVOID
            result.reasoning = "FDA boxed warning present — highest severity."
        elif fda.get("contraindications"):
            result.verdict = CAUTION
            result.reasoning = "Contraindication information found in FDA label."
        else:
            result.verdict = CAUTION
            result.reasoning = "FDA label available; specific medical guidance recommended."
        result.confidence = "HIGH"
        result.retrieval_status = "LABEL_FOUND"
        return result

    # ── INTERACTION: pair-by-pair analysis ─────────────────────────────────
    if intent_str == "interaction":
        # Check acetaminophen + alcohol special case
        aceta_check = _check_acetaminophen_alcohol(drug_names, query)
        if aceta_check:
            result.verdict, result.reasoning = aceta_check
            result.confidence = "HIGH"
            result.is_deterministic = True
            result.retrieval_status = "LABEL_FOUND" if has_any_label else "LABEL_NOT_FOUND"
            return result

        if len(drug_names) < 2:
            result.verdict = CONSULT_PHARMACIST
            result.reasoning = "Only one drug identified — cannot evaluate interactions."
            result.confidence = "MEDIUM"
            result.retrieval_status = "LABEL_FOUND" if has_any_label else "LABEL_NOT_FOUND"
            return result

        verdict, reasoning, summary = _evaluate_all_pairs(drug_names, api_results)
        result.verdict = verdict
        result.reasoning = reasoning
        result.interaction_pairs = summary
        result.confidence = "HIGH" if (summary["avoid_pairs"] or has_any_label) else "MEDIUM"
        result.retrieval_status = "LABEL_FOUND" if has_any_label else "LABEL_NOT_FOUND"

        # Check if this was a deterministic lookup
        if len(drug_names) == 2:
            det = _check_deterministic_pair(drug_names[0], drug_names[1])
            if det:
                result.is_deterministic = True

        return result

    # ── FOOD_ALCOHOL: default SAFE unless API flags issue ─────────────────
    if intent_str == "food_alcohol":
        # Check acetaminophen + alcohol
        aceta_check = _check_acetaminophen_alcohol(drug_names, query)
        if aceta_check:
            result.verdict, result.reasoning = aceta_check
            result.confidence = "HIGH"
            result.retrieval_status = "LABEL_FOUND" if has_any_label else "LABEL_NOT_FOUND"
            return result

        # Check FDA label for food/alcohol warnings
        fda = api_results.fda_labels.get(primary_drug, {})
        ixn_text = (fda.get("drug_interactions") or "").lower()
        warnings_text = (fda.get("warnings") or "").lower()
        combined = ixn_text + " " + warnings_text

        if any(w in combined for w in ("alcohol", "ethanol")):
            if any(sig in combined for sig in AVOID_SIGNALS):
                result.verdict = AVOID
                result.reasoning = "FDA label warns against alcohol use with this drug."
            else:
                result.verdict = CAUTION
                result.reasoning = "FDA label mentions alcohol interaction."
        elif any(w in combined for w in ("food", "empty stomach", "grapefruit", "dairy")):
            result.verdict = CAUTION
            result.reasoning = "FDA label mentions food-related precautions."
        else:
            result.verdict = SAFE
            result.reasoning = "No food/alcohol interaction flagged in FDA label."

        result.confidence = "HIGH" if has_primary_label else "MEDIUM"
        result.retrieval_status = "LABEL_FOUND" if has_primary_label else "LABEL_NOT_FOUND"
        return result

    # ── WHAT_IS / SAFETY / GENERAL ────────────────────────────────────────
    if intent_str == "what_is":
        result.verdict = SAFE
        result.confidence = "HIGH" if has_primary_label else "MEDIUM"
        result.reasoning = "Informational question about drug purpose/mechanism."
        result.retrieval_status = "LABEL_FOUND" if has_primary_label else "LABEL_NOT_FOUND"
        return result

    if intent_str == "safety":
        # Check if the query mentions a specific condition
        fda = api_results.fda_labels.get(primary_drug, {})
        if fda.get("boxed_warning"):
            result.verdict = CAUTION
            result.reasoning = "FDA boxed warning present — use with awareness."
        elif fda.get("warnings"):
            result.verdict = CAUTION
            result.reasoning = "FDA warnings section has relevant safety information."
        else:
            result.verdict = SAFE
            result.reasoning = "No specific safety concerns flagged in available data."
        result.confidence = "HIGH" if has_primary_label else "MEDIUM"
        result.retrieval_status = "LABEL_FOUND" if has_primary_label else "LABEL_NOT_FOUND"
        return result

    # ── GENERAL fallback ──────────────────────────────────────────────────
    result.verdict = SAFE
    result.confidence = "MEDIUM"
    result.reasoning = "General informational query."
    result.retrieval_status = "LABEL_FOUND" if has_primary_label else "LABEL_NOT_FOUND"
    return result
