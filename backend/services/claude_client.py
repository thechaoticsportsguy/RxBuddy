"""Single reusable AsyncAnthropic client for all Claude API calls."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import anthropic

from core.config import settings
from core.cache import answer_cache_get, answer_cache_set
from exceptions import ClaudeError

logger = logging.getLogger("rxbuddy.claude")

# ── Singleton async client ───────────────────────────────────────────────────
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        key = settings.ANTHROPIC_API_KEY.strip()
        if not key:
            raise ClaudeError("ANTHROPIC_API_KEY is missing")
        _client = anthropic.AsyncAnthropic(api_key=key, timeout=45.0)
    return _client


# ── Utility ──────────────────────────────────────────────────────────────────

def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words]).strip() if len(words) > max_words else text.strip()


# ── Validation (Haiku) ──────────────────────────────────────────────────────

async def validate_ai_answer(question: str, answer: str) -> str:
    """Use Claude Haiku to check verdict consistency post-generation."""
    try:
        client = _get_client()
        validation_prompt = f"""Check this medication answer for accuracy and verdict consistency:

ORIGINAL QUESTION: {question}
ANSWER TO CHECK: {answer}

Verify:
1. Does the answer ONLY mention drugs from the original question?
2. Does it directly answer the question type (dosing/interaction/pregnancy/side effects)?
3. Is there any unrelated medication mentioned?
4. Does the VERDICT match the explanation?
5. If the explanation mentions a moderate interaction, monitoring, kidney strain, lactic acidosis risk, or "use with caution", the VERDICT must be CAUTION and never SAFE.
6. If the explanation mentions a serious interaction, contraindication, major bleeding risk, or "do not take together", the VERDICT must be AVOID.

If any issue found → rewrite the answer correctly following the same output structure.
If answer is correct → return it unchanged.
If any issue is found, rewrite using EXACTLY this structure:
VERDICT: ...
ANSWER: [one decisive sentence]
WARNING: [safety warning — omit line if SAFE with no caveats]
DETAILS: fact 1 | fact 2 | fact 3
ACTION: action 1 | action 2 | action 3
ARTICLE: [1-3 sentence mechanism/context explanation]
CONFIDENCE: ...
SOURCES: ...
Return ONLY the final answer, no commentary."""

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": validation_prompt}],
        )
        return (response.content[0].text or "").strip() or answer
    except Exception:
        return answer


async def is_valid_pharmacy_question(question: str) -> bool:
    """Ask Haiku if this is a valid pharmacy question worth saving."""
    try:
        client = _get_client()
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    f"Is this a valid pharmacy/medication question worth saving to a medical FAQ database? "
                    f"Answer only YES or NO.\n\nQuestion: {question}"
                ),
            }],
        )
        text = (response.content[0].text or "").strip().upper()
        return text.startswith("YES")
    except Exception as e:
        logger.warning("[Claude] Failed to validate question: %s", e)
        return False


async def get_best_category(question: str, categories: list[str]) -> str:
    """Ask Haiku to classify a question into one of the given categories."""
    try:
        client = _get_client()
        categories_str = ", ".join(categories)
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this pharmacy question into exactly one category.\n"
                    f"Categories: {categories_str}\n\n"
                    f"Question: {question}\n\n"
                    f"Reply with only the category name, nothing else."
                ),
            }],
        )
        text = (response.content[0].text or "").strip()
        for cat in categories:
            if cat.lower() in text.lower():
                return cat
        return "General"
    except Exception as e:
        logger.warning("[Claude] Failed to categorize question: %s", e)
        return "General"


# ── Main answer generation ───────────────────────────────────────────────────

async def generate_ai_answer(
    question: str,
    drug_names: list[str],
    drug_name: str | None,
    intent_str: str,
    fda_context: str,
    citations_dicts: list[dict],
    retrieval_status_str: str,
    proceed: bool,
    risky_pair: tuple | None,
) -> tuple[str, list[dict], str, str]:
    """
    Generate a grounded answer using Claude Sonnet.

    Returns (answer_text, citations, intent_str, retrieval_status_str).
    """
    from answer_engine import (
        QuestionIntent,
        RetrievalStatus,
        build_citations,
        build_emergency_answer,
        build_intent_prompt,
        build_refused_answer,
        build_unknown_drug_answer,
        check_retrieval_guard,
        detect_emergency,
        enforce_verdict_by_intent,
        strip_off_topic_drugs,
    )
    from domain.verdicts import check_vague, is_wrong_drug_answer
    from services.fda_client import extract_drug_names

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = question.strip().lower()
    cached = await answer_cache_get(cache_key)
    if cached:
        logger.info("[Cache] HIT for: %.80s", question)
        return cached

    if not proceed:
        refused = build_refused_answer(
            QuestionIntent(intent_str) if intent_str in QuestionIntent._value2member_map_ else QuestionIntent.GENERAL,
            datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        )
        refused_text = (
            f"VERDICT: INSUFFICIENT_DATA\n"
            f"ANSWER: {refused.short_answer}\n"
            f"WARNING: This type of question requires an official FDA drug label — which was not available.\n"
            f"DETAILS: Dosing, contraindication, and pregnancy questions require verified label data | "
            f"RxBuddy does not answer these from general knowledge | "
            f"Your pharmacist or prescriber has access to complete label information\n"
            f"ACTION: {' | '.join(refused.what_to_do)}\n"
            f"ARTICLE: For questions about dosing, contraindications, and pregnancy safety, "
            f"RxBuddy requires the official FDA-approved drug label as its source.\n"
            f"CONFIDENCE: LOW\n"
            f"SOURCES: DailyMed (dailymed.nlm.nih.gov)"
        )
        return refused_text, [c.model_dump() for c in refused.citations], intent_str, retrieval_status_str

    try:
        client = _get_client()
        system_prompt, user_content = build_intent_prompt(
            intent_str=intent_str,
            question=question,
            drug_names=drug_names,
            drug_name=drug_name or "",
            fda_context=fda_context,
        )

        logger.info("[Claude] Intent=%s — generating answer for: %.80s...", intent_str, question)

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=700,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        if not response.content:
            raise ClaudeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            raise ClaudeError("Claude returned an empty response.")

        # ── Parse JSON response ──────────────────────────────────────────────
        try:
            _raw = text.strip()
            if _raw.startswith("```"):
                _raw = "\n".join(
                    line for line in _raw.splitlines() if not line.strip().startswith("```")
                ).strip()
            _parsed = json.loads(_raw)
            _verdict = str(_parsed.get("verdict", "CONSULT_PHARMACIST")).upper().strip()
            _explanation = str(_parsed.get("explanation", "")).strip()
            _valid = {"SAFE", "CAUTION", "AVOID", "CONSULT_PHARMACIST"}
            if _verdict not in _valid:
                _verdict = "CONSULT_PHARMACIST"
            text = (
                f"VERDICT: {_verdict}\n"
                f"ANSWER: {_explanation}\n"
                f"CONFIDENCE: HIGH\n"
                f"SOURCES: FDA label (DailyMed) | RxNorm | openFDA"
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        answer = _truncate_words(text, 400)

        if check_vague(answer):
            logger.warning("[QualityCheck] Vague DIRECT detected — sending to validator")
        answer = await validate_ai_answer(question, answer)

        answer = enforce_verdict_by_intent(intent_str, answer)
        answer = strip_off_topic_drugs(drug_names, answer, intent_str)

        if drug_names and is_wrong_drug_answer(question, answer, extract_drug_names):
            logger.warning("[QualityCheck] Claude returned wrong-drug answer — using CONSULT fallback")
            answer = (
                f"VERDICT: CONSULT_PHARMACIST\n"
                f"ANSWER: We could not generate a reliable answer for this medication. Please consult a pharmacist.\n"
                f"WARNING: Our system could not verify the drug information matched your query.\n"
                f"DETAILS: Drug name could not be confirmed in the response | A pharmacist can provide accurate information\n"
                f"ACTION: Consult a licensed pharmacist | Check DailyMed at dailymed.nlm.nih.gov\n"
                f"ARTICLE: For accurate medication information, a licensed pharmacist is the best resource.\n"
                f"CONFIDENCE: LOW\n"
                f"SOURCES: Pharmacist consultation recommended"
            )

        logger.info("[Claude] Answer ready (%d words)", len(answer.split()))
        await answer_cache_set(cache_key, answer, citations_dicts, intent_str, retrieval_status_str)
        return answer, citations_dicts, intent_str, retrieval_status_str

    except anthropic.APIError as e:
        logger.error("[Claude] API Error: %s (status=%s)", e.message, getattr(e, "status_code", "N/A"))
        raise ClaudeError(e.message, getattr(e, "status_code", None))
    except ClaudeError:
        raise
    except Exception as e:
        logger.error("[Claude] Unexpected error: %s", str(e), exc_info=True)
        raise ClaudeError(str(e))
