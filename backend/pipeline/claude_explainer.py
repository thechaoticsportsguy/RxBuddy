"""
Pipeline Step 6 — Claude Explanation Generator (STRICTLY LIMITED).

Claude is ONLY used to generate human-readable explanation text.
It MUST NOT decide the verdict. The backend decision engine (Step 5)
has already computed the verdict before this step runs.

Claude receives:
  - The intent
  - The drug names
  - The computed verdict (FINAL — Claude cannot change it)
  - A summary of API evidence

Claude returns:
  - explanation: 1-2 sentence plain-English answer
  - key_points:  2-3 bullet points of clinical facts
  - warning:     1 sentence safety warning (empty if SAFE)
  - action:      2-3 action items for the patient

If Claude fails or is unavailable, we fall back to the decision engine's
own reasoning text. The system is NEVER blocked by Claude availability.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("rxbuddy.pipeline.claude_explainer")


@dataclass
class Explanation:
    """Structured explanation output from Claude."""
    answer: str = ""            # 1-2 sentence primary answer
    warning: str = ""           # 1 sentence safety warning (empty if SAFE)
    details: list[str] = field(default_factory=list)   # 2-3 clinical fact bullets
    action: list[str] = field(default_factory=list)    # 2-3 action items
    article: str = ""           # 1-3 sentence mechanism/context paragraph
    from_claude: bool = False   # True if Claude generated this; False if fallback


def _get_api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return key or None


def _build_context_summary(
    intent: str,
    drug_names: list[str],
    verdict: str,
    reasoning: str,
    fda_labels: dict,
    rxnav_interactions: list[dict],
    adverse_events: dict,
    recalls: dict,
) -> str:
    """Build a concise evidence summary for Claude's context window."""
    parts = []

    parts.append(f"BACKEND VERDICT (FINAL — do NOT override): {verdict}")
    parts.append(f"INTENT: {intent}")
    parts.append(f"DRUGS: {', '.join(drug_names)}")
    parts.append(f"REASONING: {reasoning}")

    # FDA label excerpts (keep short — Claude only needs enough to explain)
    for drug, fda in fda_labels.items():
        drug_parts = [f"\n--- FDA LABEL: {drug.upper()} ---"]
        for section in ("warnings", "boxed_warning", "drug_interactions",
                        "adverse_reactions", "indications_and_usage",
                        "dosage_and_administration", "contraindications",
                        "pregnancy", "description"):
            text = fda.get(section, "")
            if text:
                drug_parts.append(f"{section.upper()}: {text[:400]}")
        if len(drug_parts) > 1:
            parts.append("\n".join(drug_parts))

    # RxNav interactions
    if rxnav_interactions:
        parts.append("\n--- RXNAV VETTED INTERACTIONS ---")
        for ix in rxnav_interactions[:3]:
            sev = (ix.get("severity") or "unknown").upper()
            desc = ix.get("description", "")[:200]
            parts.append(f"[{sev}] {desc}")

    # FAERS adverse events
    for drug, events in adverse_events.items():
        if events:
            parts.append(f"\n--- FAERS TOP REACTIONS ({drug}) ---")
            parts.append(", ".join(events[:10]))

    # Recalls
    for drug, recs in recalls.items():
        if recs:
            parts.append(f"\n--- ACTIVE RECALLS ({drug}) ---")
            for r in recs[:2]:
                parts.append(f"[{r.get('classification','')}] {r.get('reason_for_recall','')[:150]}")

    return "\n".join(parts)


# ── System prompts per intent ─────────────────────────────────────────────────
# Each prompt forces Claude to explain the GIVEN verdict, never override it.

_SYSTEM_PROMPT_TEMPLATE = """You are a clinical pharmacist assistant for RxBuddy.
Your ONLY job is to explain a medical verdict that has ALREADY been decided.
You MUST NOT change the verdict. The verdict is: {verdict}

RULES:
1. The verdict "{verdict}" is FINAL. Do not suggest a different verdict.
2. Use ONLY the FDA label data and API evidence provided below. Do not add information from your training data.
3. Only mention drugs that appear in the user's question: {drugs}
4. Do not mention any other drug names.
5. Keep language simple and direct — no medical jargon unless necessary.
6. No markdown, no asterisks, no bullet symbols, no headers.

Return ONLY valid JSON with this exact structure:
{{"explanation": "1-2 sentence answer that aligns with the {verdict} verdict",
  "key_points": ["clinical fact 1", "clinical fact 2"],
  "warning": "1 sentence safety warning (empty string if SAFE)",
  "action": ["what to do 1", "what to do 2"]}}

EVIDENCE:
{context}"""


def generate_explanation(
    intent: str,
    drug_names: list[str],
    verdict: str,
    reasoning: str,
    fda_labels: dict,
    rxnav_interactions: list[dict] | None = None,
    adverse_events: dict | None = None,
    recalls: dict | None = None,
    query: str = "",
) -> Explanation:
    """
    Generate a human-readable explanation using Claude.
    Claude explains the verdict — it does NOT decide it.

    If Claude fails or API key is missing, returns a fallback explanation
    built from the decision engine's own reasoning.

    Parameters
    ----------
    intent              : classified intent string
    drug_names          : normalised drug names
    verdict             : the FINAL verdict from decision_engine.compute_verdict()
    reasoning           : decision engine's brief reasoning
    fda_labels          : dict of drug→fda_data from api_layer
    rxnav_interactions  : list of RxNav interaction dicts
    adverse_events      : dict of drug→[reaction terms]
    recalls             : dict of drug→[recall dicts]
    query               : original user query

    Returns
    -------
    Explanation dataclass with answer, warning, details, action, article.
    """
    api_key = _get_api_key()

    # Build context for Claude
    context = _build_context_summary(
        intent=intent,
        drug_names=drug_names,
        verdict=verdict,
        reasoning=reasoning,
        fda_labels=fda_labels,
        rxnav_interactions=rxnav_interactions or [],
        adverse_events=adverse_events or {},
        recalls=recalls or {},
    )

    # If no API key, use fallback immediately
    if not api_key:
        logger.warning("[Claude] No API key — using fallback explanation")
        return _build_fallback(verdict, reasoning, drug_names, intent)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=8.0)

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            verdict=verdict,
            drugs=", ".join(drug_names),
            context=context,
        )

        user_message = f"Question: {query}\n\nExplain the {verdict} verdict for {', '.join(drug_names)}."

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        if not response.content:
            raise RuntimeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            raise RuntimeError("Claude returned empty text.")

        # Parse JSON response
        raw = text
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```")).strip()

        parsed = json.loads(raw)

        result = Explanation(
            answer=str(parsed.get("explanation", ""))[:300],
            warning=str(parsed.get("warning", ""))[:200],
            details=list(parsed.get("key_points", []))[:3],
            action=list(parsed.get("action", []))[:3],
            article=str(parsed.get("explanation", ""))[:300],
            from_claude=True,
        )

        logger.info("[Claude] Explanation generated (%d chars)", len(result.answer))
        return result

    except json.JSONDecodeError:
        logger.warning("[Claude] JSON parse failed — using fallback")
        return _build_fallback(verdict, reasoning, drug_names, intent)
    except Exception as exc:
        logger.warning("[Claude] Failed: %s — using fallback", exc)
        return _build_fallback(verdict, reasoning, drug_names, intent)


def _build_fallback(
    verdict: str,
    reasoning: str,
    drug_names: list[str],
    intent: str,
) -> Explanation:
    """
    Build a fallback explanation when Claude is unavailable.
    Uses the decision engine's own reasoning text.
    """
    drugs_str = " and ".join(drug_names) if drug_names else "this medication"

    # Map verdict to template answer
    if verdict == "AVOID":
        answer = f"Do not combine {drugs_str} without medical supervision. {reasoning}"
        warning = "This combination carries significant risk. Consult your prescriber."
        action = ["Do not take these together without doctor approval",
                   "Contact your prescriber before making changes"]
    elif verdict == "CAUTION":
        answer = f"Use {drugs_str} with caution. {reasoning}"
        warning = "Monitor for adverse effects and consult your provider."
        action = ["Follow your prescriber's instructions",
                   "Report any unusual symptoms to your doctor"]
    elif verdict == "SAFE":
        answer = f"Based on available data, {drugs_str} can generally be used as directed. {reasoning}"
        warning = ""
        action = ["Follow the directions on your prescription or label",
                   "Contact your pharmacist with any questions"]
    elif verdict == "CONSULT_PHARMACIST":
        answer = f"We recommend consulting a pharmacist about {drugs_str}. {reasoning}"
        warning = "Professional guidance is recommended for this question."
        action = ["Speak with your pharmacist or prescriber",
                   "Check DailyMed at dailymed.nlm.nih.gov for official label information"]
    else:
        answer = reasoning or f"Please consult a pharmacist about {drugs_str}."
        warning = "Professional guidance recommended."
        action = ["Consult a licensed pharmacist"]

    return Explanation(
        answer=answer[:300],
        warning=warning,
        details=[reasoning[:150]] if reasoning else [],
        action=action,
        article=answer[:300],
        from_claude=False,
    )
