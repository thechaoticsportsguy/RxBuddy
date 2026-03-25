"""
Pipeline Step 9 — Response Validator (Anti-Hallucination Shield).

Runs BEFORE any side-effect response is returned to the user.
Ensures ZERO hallucinated content reaches the user.

Every side effect entry MUST have a traceable source (source_section
and label_source). If any entry is missing these fields, it gets removed.

Checks:
  1. Source verification — every item must have source_section + label_source
  2. No AI-generated content in side effects
  3. Consistency — same term in common + serious → keep the more severe only
  4. Empty response handling — never return empty lists silently
  5. Confidence tagging — high / moderate / no_data based on label freshness
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("rxbuddy.pipeline.response_validator")


def validate_side_effect_response(response: dict) -> dict:
    """
    Validate a structured side-effect response before sending to the user.

    Ensures every side effect entry is traceable to a real FDA label section.
    Removes anything that looks AI-generated or lacks a source.

    Parameters
    ----------
    response : dict
        The full structured answer dict (the 'structured' key from the pipeline).

    Returns
    -------
    dict — the validated (and possibly trimmed) response, plus answer_metadata.
    """
    # Only validate side-effect responses
    if response.get("intent") != "side_effects":
        return response

    se = response.get("side_effects_structured", {})
    if not se:
        # Nothing to validate — tag as no_data
        response["answer_metadata"] = _build_metadata(
            source_type="none",
            confidence="no_data",
            ai_generated=False,
            validation_passed=True,
            label_age_days=None,
        )
        return response

    # ── CHECK 1: Source verification ─────────────────────────────────────────
    # Every side effect entry MUST have source_section and label_source.
    # If missing → remove it and log a warning.
    removed_count = 0
    for category in ("common", "serious", "boxed_warning"):
        items = se.get(category, [])
        verified = []
        for item in items:
            if not isinstance(item, dict):
                # Old-style plain string — not verified, remove it
                removed_count += 1
                logger.warning("[Validator] Removed unstructured item from %s: %r", category, item)
                continue
            if not item.get("source_section") or not item.get("label_source"):
                removed_count += 1
                logger.warning("[Validator] Removed unsourced item from %s: %r", category, item.get("term", "unknown"))
                continue
            verified.append(item)
        se[category] = verified

    # ── CHECK 2: No AI-generated content ─────────────────────────────────────
    # If any entry was flagged as AI-generated, remove it.
    for category in ("common", "serious", "boxed_warning"):
        items = se.get(category, [])
        clean = []
        for item in items:
            if item.get("ai_generated", False):
                removed_count += 1
                logger.warning("[Validator] Removed AI-generated item from %s: %r", category, item.get("term", "unknown"))
                continue
            clean.append(item)
        se[category] = clean

    # ── CHECK 3: Consistency — deduplicate across severity tiers ─────────────
    # If the same side effect appears in both "common" and "serious",
    # keep only the more severe classification.
    serious_terms = {item["term"].lower() for item in se.get("serious", []) if "term" in item}
    boxed_terms = {item["term"].lower() for item in se.get("boxed_warning", []) if "term" in item}

    # Remove from common if also in serious or boxed
    se["common"] = [
        item for item in se.get("common", [])
        if item.get("term", "").lower() not in serious_terms
        and item.get("term", "").lower() not in boxed_terms
    ]
    # Remove from serious if also in boxed
    se["serious"] = [
        item for item in se.get("serious", [])
        if item.get("term", "").lower() not in boxed_terms
    ]

    # ── CHECK 4: Empty response handling ─────────────────────────────────────
    # If after validation there are ZERO side effects, return a clear "no data" message.
    total_items = (
        len(se.get("common", []))
        + len(se.get("serious", []))
        + len(se.get("boxed_warning", []))
    )

    if total_items == 0:
        drug_name = response.get("generic_name", response.get("drug", "this medication"))
        response["answer"] = (
            f"No reliable label data found for {drug_name}. "
            "Consult your pharmacist for side effect information."
        )
        response["retrieval_status"] = "NO_LABEL_DATA"
        response["answer_metadata"] = _build_metadata(
            source_type="none",
            confidence="no_data",
            ai_generated=False,
            validation_passed=True,
            label_age_days=None,
        )
        logger.info("[Validator] All items removed after validation — returning no_data for %s", drug_name)
        return response

    # ── CHECK 5: Confidence tagging ──────────────────────────────────────────
    label_info = response.get("label_info", {})
    revision_date_str = label_info.get("revision_date", "")
    label_age_days = _compute_label_age(revision_date_str)

    if label_age_days is not None and label_age_days <= 730:
        confidence = "high"
    elif label_age_days is not None:
        confidence = "moderate"
    else:
        confidence = "moderate"  # we have data but no date info

    response["side_effects_structured"] = se
    response["answer_metadata"] = _build_metadata(
        source_type="structured_label_data",
        confidence=confidence,
        ai_generated=False,
        validation_passed=True,
        label_age_days=label_age_days,
    )

    if removed_count > 0:
        logger.info("[Validator] Removed %d unsourced/AI items from response", removed_count)

    return response


def _build_metadata(
    source_type: str,
    confidence: str,
    ai_generated: bool,
    validation_passed: bool,
    label_age_days: int | None,
) -> dict:
    """Build the answer_metadata block for the response."""
    return {
        "source_type": source_type,
        "confidence": confidence,
        "ai_generated": ai_generated,
        "validation_passed": validation_passed,
        "label_age_days": label_age_days,
    }


def _compute_label_age(revision_date_str: str) -> int | None:
    """
    Compute how many days old a label revision date is.

    Parameters
    ----------
    revision_date_str : str
        Date in 'YYYY-MM-DD' format, or empty string.

    Returns
    -------
    int (days) or None if date is missing/unparseable.
    """
    if not revision_date_str:
        return None
    try:
        rev_date = datetime.strptime(revision_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - rev_date).days
    except (ValueError, TypeError):
        return None
