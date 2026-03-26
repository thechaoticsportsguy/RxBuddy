"""
Pipeline Step 7 — Hard Verdict Enforcer.

After Claude generates its explanation, this step FORCES the backend
verdict onto the final response. If Claude somehow smuggled a different
verdict into its text, we overwrite it.

This is the final checkpoint before the response is returned to the user.
The UI verdict MUST match the backend verdict. No exceptions.
"""
from __future__ import annotations

import logging
import re

from pipeline.claude_explainer import Explanation

logger = logging.getLogger("rxbuddy.pipeline.verdict_enforcer")


# Phrases that indicate a verdict mismatch (Claude tried to override)
_AVOID_PHRASES = (
    "avoid", "do not take", "do not combine", "contraindicated",
    "dangerous combination", "life-threatening", "fatal",
)
_SAFE_PHRASES = (
    "no interaction", "no known interaction", "safe to take",
    "generally safe", "no significant interaction", "compatible",
)


def enforce_verdict(
    backend_verdict: str,
    explanation: Explanation,
    drug_names: list[str],
    intent: str,
) -> Explanation:
    """
    FORCE the backend verdict onto the explanation.

    Rules:
    1. Strip any verdict text Claude may have inserted
    2. Remove mentions of drugs NOT in the original query
    3. If explanation contradicts the verdict, replace with fallback text
    4. Ensure warning field aligns with verdict

    Parameters
    ----------
    backend_verdict : the FINAL verdict from decision_engine
    explanation     : Claude's generated explanation
    drug_names      : list of drugs from the original query
    intent          : classified intent

    Returns
    -------
    Cleaned Explanation that is guaranteed to align with backend_verdict.
    """
    answer = explanation.answer
    warning = explanation.warning

    # ── 1. Strip any verdict labels Claude may have emitted ───────────────
    for pattern in (r"VERDICT:\s*\S+", r"verdict:\s*\S+", r"\bSAFE\b", r"\bAVOID\b",
                    r"\bCAUTION\b", r"\bCONSULT[\s_]PHARMACIST\b"):
        # Only strip if it appears at the start of a sentence or as a label
        answer = re.sub(r"^" + pattern, "", answer, flags=re.IGNORECASE).strip()

    # ── 2. Check for verdict contradiction ────────────────────────────────
    answer_lower = answer.lower()

    if backend_verdict == "SAFE":
        # If Claude's text says "avoid" or "do not take", that contradicts SAFE
        if any(p in answer_lower for p in _AVOID_PHRASES):
            logger.warning("[VerdictEnforcer] SAFE verdict but explanation contains AVOID language — rewriting")
            answer = _rewrite_for_verdict(backend_verdict, drug_names, intent)
            warning = ""

    elif backend_verdict == "AVOID":
        # If Claude's text says "safe" or "no interaction", that contradicts AVOID
        if any(p in answer_lower for p in _SAFE_PHRASES):
            logger.warning("[VerdictEnforcer] AVOID verdict but explanation contains SAFE language — rewriting")
            answer = _rewrite_for_verdict(backend_verdict, drug_names, intent)
            warning = "This combination carries significant risk."

    elif backend_verdict == "CAUTION":
        # CAUTION shouldn't say "no interaction" or "safe to take"
        if any(p in answer_lower for p in _SAFE_PHRASES):
            logger.warning("[VerdictEnforcer] CAUTION verdict but explanation says SAFE — rewriting")
            answer = _rewrite_for_verdict(backend_verdict, drug_names, intent)

    # ── 3. Ensure warning field matches verdict ───────────────────────────
    if backend_verdict == "SAFE" and warning:
        # SAFE should have no warning (or a very mild one)
        if any(w in warning.lower() for w in ("avoid", "danger", "risk", "do not")):
            warning = ""

    if backend_verdict in ("AVOID", "CAUTION") and not warning:
        if backend_verdict == "AVOID":
            warning = "This combination carries significant risk. Consult your prescriber."
        else:
            warning = "Use with caution and monitor for adverse effects."

    # ── 4. Remove off-topic drug names ────────────────────────────────────
    answer = _strip_off_topic_drugs(answer, drug_names)
    for i, detail in enumerate(explanation.details):
        explanation.details[i] = _strip_off_topic_drugs(detail, drug_names)

    explanation.answer = answer
    explanation.warning = warning
    explanation.article = answer  # keep in sync

    return explanation


def _strip_off_topic_drugs(text: str, allowed_drugs: list[str]) -> str:
    """
    Remove sentences that mention drugs NOT in the user's query.
    This prevents hallucinated drug mentions from leaking through.
    """
    if not text or not allowed_drugs:
        return text

    # Build set of allowed names (include brand variants)
    allowed = set(d.lower() for d in allowed_drugs)
    try:
        from drug_catalog import find_drug
        for drug in allowed_drugs:
            rec = find_drug(drug)
            if rec:
                for bn in rec.brand_names:
                    allowed.add(bn.lower())
    except Exception:
        pass

    # Common off-topic drug names that might appear
    common_drugs = {
        "warfarin", "aspirin", "ibuprofen", "naproxen", "metformin",
        "lisinopril", "sertraline", "fluoxetine", "tramadol", "lithium",
        "digoxin", "amiodarone", "methotrexate", "sildenafil",
    }
    off_topic = common_drugs - allowed

    # Remove sentences containing off-topic drugs
    sentences = re.split(r'(?<=[.!?])\s+', text)
    cleaned = []
    for s in sentences:
        s_lower = s.lower()
        if any(drug in s_lower for drug in off_topic if len(drug) >= 4):
            # Only remove if the drug is NOT in the allowed set
            if not any(a in s_lower for a in allowed if len(a) >= 4):
                logger.debug("[VerdictEnforcer] Stripped off-topic sentence: %.60s", s)
                continue
        cleaned.append(s)

    return " ".join(cleaned) if cleaned else text


def enforce_verdict_for_red_flags(
    current_verdict: str,
    se_data: dict,
) -> str:
    """
    If any side effect in the parsed side_effects_data has red_flag=True,
    upgrade the verdict to CONSULT_PHARMACIST.

    This runs AFTER the main verdict_enforcer so it can only upgrade,
    never downgrade, an existing verdict.
    """
    if not se_data:
        return current_verdict

    # Already at max-severity verdict — nothing to do
    if current_verdict in ("AVOID", "EMERGENCY"):
        return current_verdict

    # Check every tier for red-flagged effects
    side_effects = se_data.get("side_effects", {})
    for tier_data in side_effects.values():
        for item in tier_data.get("items", []):
            if isinstance(item, dict) and item.get("red_flag"):
                if current_verdict in ("SAFE", "CAUTION"):
                    logger.info(
                        "[VerdictEnforcer] Red flag detected (%s) — upgrading %s → CONSULT_PHARMACIST",
                        item.get("display_name", "unknown"),
                        current_verdict,
                    )
                    return "CONSULT_PHARMACIST"

    # Also check has_red_flag shortcut
    if se_data.get("has_red_flag") and current_verdict in ("SAFE", "CAUTION"):
        logger.info("[VerdictEnforcer] has_red_flag=True — upgrading %s → CONSULT_PHARMACIST",
                    current_verdict)
        return "CONSULT_PHARMACIST"

    return current_verdict


def _rewrite_for_verdict(verdict: str, drug_names: list[str], intent: str) -> str:
    """Generate a safe fallback answer that matches the verdict."""
    drugs = " and ".join(drug_names) if drug_names else "these medications"

    if verdict == "SAFE":
        return f"Based on available data, {drugs} can generally be used together as directed."
    elif verdict == "AVOID":
        return f"Do not combine {drugs} without medical supervision — the combination carries significant risk."
    elif verdict == "CAUTION":
        return f"Use {drugs} with caution and monitor for adverse effects."
    elif verdict == "CONSULT_PHARMACIST":
        return f"Consult a pharmacist for guidance on {drugs}."
    return f"Please consult a pharmacist about {drugs}."
