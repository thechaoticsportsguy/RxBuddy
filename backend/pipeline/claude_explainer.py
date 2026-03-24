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
    # ── Side-effects specific fields (only populated for side_effects intent) ──
    common_side_effects: list[str] = field(default_factory=list)
    serious_side_effects: list[str] = field(default_factory=list)
    warning_signs: list[str] = field(default_factory=list)
    higher_risk_groups: list[str] = field(default_factory=list)
    what_to_do: list[str] = field(default_factory=list)


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


# ── Side-effects context builder (omits drug_interactions to prevent leakage) ──

def _build_side_effects_context(
    drug_names: list[str],
    verdict: str,
    reasoning: str,
    fda_labels: dict,
    adverse_events: dict,
) -> str:
    """Build a concise side-effects evidence summary for Claude. No drug_interactions."""
    parts = []
    parts.append(f"BACKEND VERDICT (FINAL): {verdict}")
    parts.append(f"DRUGS: {', '.join(drug_names)}")
    parts.append(f"REASONING: {reasoning}")

    for drug, fda in fda_labels.items():
        drug_parts = [f"\n--- FDA LABEL: {drug.upper()} ---"]
        for section in ("adverse_reactions", "boxed_warning", "warnings",
                        "precautions", "patient_counseling_information"):
            text = fda.get(section, "")
            if text:
                drug_parts.append(f"{section.upper()}: {text[:600]}")
        if len(drug_parts) > 1:
            parts.append("\n".join(drug_parts))

    for drug, events in adverse_events.items():
        if events:
            parts.append(f"\n--- FAERS TOP REACTIONS ({drug.upper()}) ---")
            parts.append(", ".join(events[:15]))

    return "\n".join(parts)


# ── Per-drug hardcoded side-effects (used when Claude is unavailable) ──────────

_DRUG_SE_FALLBACK: dict[str, dict] = {
    "metformin": {
        "common": [
            "Nausea or upset stomach (most common when starting)",
            "Diarrhea or loose stools",
            "Stomach pain or cramping",
            "Loss of appetite",
            "Metallic taste in the mouth",
        ],
        "serious": [
            "Lactic acidosis (rare but serious) — symptoms: unusual muscle pain, weakness, trouble breathing",
            "Vitamin B12 deficiency with long-term use",
        ],
        "warning_signs": [
            "Unusual muscle pain or weakness",
            "Difficulty breathing or fast or slow breathing",
            "Stomach pain with nausea or vomiting",
            "Feeling very cold, dizzy, or lightheaded",
        ],
        "higher_risk": [
            "People with kidney disease",
            "Heavy alcohol users",
            "Elderly patients",
            "Anyone having surgery or imaging tests that use contrast dye",
        ],
        "what_to_do": [
            "Take metformin with food to reduce stomach side effects",
            "Start with a low dose — your doctor will increase it gradually",
            "Get regular kidney tests as your doctor recommends",
            "Tell your doctor about any unusual weakness or stomach pain",
        ],
    },
    "lisinopril": {
        "common": [
            "Dry, persistent cough (affects up to 1 in 5 people)",
            "Dizziness or lightheadedness",
            "Headache",
            "Fatigue or tiredness",
            "Nausea",
        ],
        "serious": [
            "Angioedema — sudden swelling of the face, lips, tongue, or throat (rare, requires emergency care)",
            "High potassium levels (hyperkalemia)",
            "Decline in kidney function",
        ],
        "warning_signs": [
            "Sudden or severe swelling of the face, lips, tongue, or throat",
            "Severe dizziness or fainting",
            "Decreased urination or leg swelling (may signal kidney problems)",
            "Chest pain",
        ],
        "higher_risk": [
            "People with pre-existing kidney disease",
            "Those also taking potassium supplements or salt substitutes",
            "People with a history of angioedema",
        ],
        "what_to_do": [
            "The cough is a known, common side effect — talk to your doctor if it bothers you (an alternative can be prescribed)",
            "Rise slowly when standing up to prevent dizziness",
            "Monitor your blood pressure regularly",
            "Avoid potassium supplements unless your doctor says otherwise",
        ],
    },
    "methotrexate": {
        "common": [
            "Nausea and vomiting",
            "Mouth sores or ulcers",
            "Fatigue or tiredness",
            "Loss of appetite",
            "Diarrhea",
        ],
        "serious": [
            "Liver damage (with long-term use)",
            "Lung inflammation (shortness of breath or dry cough)",
            "Low blood cell counts (increased infection risk or bleeding)",
            "Kidney damage",
        ],
        "warning_signs": [
            "Unusual bruising or bleeding",
            "Signs of infection: fever, chills, sore throat",
            "Shortness of breath or a new dry cough",
            "Yellowing of the skin or eyes (jaundice)",
        ],
        "higher_risk": [
            "People who drink alcohol",
            "Those with kidney or liver disease",
            "People also taking NSAIDs (ibuprofen, naproxen)",
            "Elderly patients",
        ],
        "what_to_do": [
            "Take folic acid supplements as directed — this significantly reduces side effects",
            "Avoid alcohol completely while on methotrexate",
            "Get all scheduled blood tests (liver, kidney, blood counts)",
            "Report any signs of infection immediately to your doctor",
        ],
    },
    "semaglutide": {
        "common": [
            "Nausea (very common especially in first weeks — usually improves over time)",
            "Vomiting",
            "Diarrhea or constipation",
            "Stomach pain or discomfort",
            "Decreased appetite",
        ],
        "serious": [
            "Pancreatitis (inflammation of the pancreas) — severe, persistent stomach pain",
            "Gallbladder problems",
            "Kidney problems if you become dehydrated from vomiting or diarrhea",
            "Risk of thyroid tumors (seen in animal studies)",
        ],
        "warning_signs": [
            "Severe stomach pain that radiates to your back and doesn't go away",
            "Vomiting that prevents keeping any fluids down",
            "Signs of low blood sugar (if combined with other diabetes meds): shakiness, sweating, confusion",
            "Lump or swelling in the neck",
        ],
        "higher_risk": [
            "People with a personal or family history of thyroid cancer",
            "Those with a history of pancreatitis",
            "People with severe kidney disease",
            "Those prone to gallbladder problems",
        ],
        "what_to_do": [
            "Dose starts low and increases slowly — nausea usually fades after 4–8 weeks",
            "Eat smaller, lower-fat meals to reduce nausea",
            "Stay well hydrated, especially if experiencing vomiting or diarrhea",
            "Tell your doctor about any severe stomach pain or vomiting that won't stop",
        ],
    },
}

# Brand name aliases
for _alias, _generic in [
    ("ozempic", "semaglutide"), ("wegovy", "semaglutide"), ("rybelsus", "semaglutide"),
    ("glucophage", "metformin"), ("fortamet", "metformin"), ("glumetza", "metformin"),
    ("zestril", "lisinopril"), ("prinivil", "lisinopril"), ("qbrelis", "lisinopril"),
    ("rheumatrex", "methotrexate"), ("otrexup", "methotrexate"), ("rasuvo", "methotrexate"),
]:
    _DRUG_SE_FALLBACK[_alias] = _DRUG_SE_FALLBACK[_generic]


def _build_fallback_side_effects(drug_names: list[str]) -> "Explanation":
    """Build a fallback side-effects explanation using hardcoded per-drug data."""
    drug = drug_names[0].lower() if drug_names else ""
    data = _DRUG_SE_FALLBACK.get(drug)
    drugs_str = " and ".join(drug_names) if drug_names else "this medication"

    if data:
        return Explanation(
            answer=f"Here are the known side effects of {drugs_str}.",
            warning="If you experience severe or unusual symptoms, contact your healthcare provider.",
            details=[],
            action=data["what_to_do"][:3],
            article=(
                f"{drugs_str.capitalize()} can cause side effects in some people. "
                "Most are mild and improve over time, but some require medical attention."
            ),
            common_side_effects=data["common"],
            serious_side_effects=data["serious"],
            warning_signs=data["warning_signs"],
            higher_risk_groups=data["higher_risk"],
            what_to_do=data["what_to_do"],
            from_claude=False,
        )

    # Generic fallback — drug not in our table
    return Explanation(
        answer=f"Like all medications, {drugs_str} can cause side effects.",
        warning="Consult your pharmacist or prescriber about side effects that concern you.",
        details=[],
        action=[
            "Tell your doctor about any new or worsening symptoms",
            "Read the medication guide that comes with your prescription",
            "Contact your pharmacist with questions",
        ],
        article=(
            f"Side effects vary by person. Your pharmacist can provide a complete list of "
            f"known side effects for {drugs_str}."
        ),
        common_side_effects=[
            "Side effects vary — consult your pharmacist or prescriber for a complete list for this medication",
        ],
        serious_side_effects=[
            "Serious side effects are possible — contact your provider if you experience unusual symptoms",
        ],
        warning_signs=[
            "Signs of a severe allergic reaction: rash, difficulty breathing, swelling of face or throat",
            "Symptoms that feel unusual or worsen unexpectedly",
        ],
        higher_risk_groups=[
            "Elderly patients",
            "People with kidney or liver disease",
            "Those taking multiple medications",
        ],
        what_to_do=[
            "Read the patient information leaflet that comes with your prescription",
            "Tell your doctor about all medications and supplements you take",
            "Report any unexpected or troubling side effects to your pharmacist or doctor",
        ],
        from_claude=False,
    )


# ── System prompts per intent ─────────────────────────────────────────────────
# Each prompt forces Claude to explain the GIVEN verdict, never override it.

_SIDE_EFFECTS_PROMPT = """You are a clinical pharmacist assistant for RxBuddy.
Your job is to explain the side effects of {drugs} in plain, patient-friendly language.
The backend has already decided the verdict is CAUTION.

RULES:
1. Use simple, everyday language — translate all medical jargon:
   somnolence → "feeling drowsy or sleepy"
   nausea → "feeling sick to your stomach"
   diarrhea → "loose or watery stools"
   dyspepsia → "indigestion or stomach upset"
   pruritus → "itching"
   myalgia → "muscle aches or pain"
   flatulence → "gas"
   alopecia → "hair loss"
   pyrexia → "fever"
   edema → "swelling"
   erythema → "redness"
   dysgeusia → "taste changes or metallic taste"
2. Use ONLY the FDA label data and FAERS adverse events provided. Do not use your training data.
3. Only mention {drugs}. Do NOT mention other drug names.
4. Do NOT mention drug interactions, drug combinations, or other drugs.
5. No markdown, no asterisks, no bullet symbols, no headers.
6. Keep each list item to 1 sentence. Be concise and specific.

Return ONLY valid JSON with this exact structure:
{{"common_side_effects": ["plain-language side effect 1", "side effect 2", "side effect 3", "side effect 4"],
  "serious_side_effects": ["serious but rare effect 1", "serious effect 2"],
  "warning_signs": ["sign to get help 1", "sign 2", "sign 3"],
  "higher_risk_groups": ["group 1", "group 2"],
  "what_to_do": ["action 1", "action 2", "action 3"]}}

EVIDENCE:
{context}"""

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

    # ── Side-effects intent gets its own dedicated flow ────────────────────────
    if intent == "side_effects":
        return _generate_side_effects_explanation(
            drug_names=drug_names,
            verdict=verdict,
            reasoning=reasoning,
            fda_labels=fda_labels,
            adverse_events=adverse_events or {},
            query=query,
            api_key=api_key,
        )

    # ── Generic explanation flow (all other intents) ───────────────────────────
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


def _generate_side_effects_explanation(
    drug_names: list[str],
    verdict: str,
    reasoning: str,
    fda_labels: dict,
    adverse_events: dict,
    query: str,
    api_key: str | None,
) -> Explanation:
    """
    Dedicated side-effects explanation generator.
    Uses a patient-friendly prompt that returns structured arrays instead of
    the generic explanation/key_points/warning/action JSON.
    """
    if not api_key:
        logger.warning("[Claude-SE] No API key — using per-drug fallback")
        return _build_fallback_side_effects(drug_names)

    context = _build_side_effects_context(
        drug_names=drug_names,
        verdict=verdict,
        reasoning=reasoning,
        fda_labels=fda_labels,
        adverse_events=adverse_events,
    )

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=10.0)

        system_prompt = _SIDE_EFFECTS_PROMPT.format(
            drugs=", ".join(drug_names),
            context=context,
        )

        user_message = f"Question: {query}\n\nList the side effects of {', '.join(drug_names)} in plain language."

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        if not response.content:
            raise RuntimeError("Claude returned no content.")

        text = (response.content[0].text or "").strip()
        if not text:
            raise RuntimeError("Claude returned empty text.")

        # Strip markdown fences
        raw = text
        if raw.startswith("```"):
            raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```")).strip()

        parsed = json.loads(raw)

        drugs_str = " and ".join(drug_names) if drug_names else "this medication"
        result = Explanation(
            answer=f"Here are the known side effects of {drugs_str}.",
            warning="Contact your healthcare provider if you experience severe or unusual symptoms.",
            details=[],
            action=list(parsed.get("what_to_do", []))[:3],
            article=f"{drugs_str.capitalize()} can cause side effects in some people. Most are mild, but some require medical attention.",
            common_side_effects=list(parsed.get("common_side_effects", []))[:5],
            serious_side_effects=list(parsed.get("serious_side_effects", []))[:3],
            warning_signs=list(parsed.get("warning_signs", []))[:4],
            higher_risk_groups=list(parsed.get("higher_risk_groups", []))[:3],
            what_to_do=list(parsed.get("what_to_do", []))[:4],
            from_claude=True,
        )

        # Guard: Claude must never return empty common_side_effects for a side_effects query.
        # If it did, backfill from the hardcoded per-drug table (or generic fallback).
        if not result.common_side_effects:
            fallback = _build_fallback_side_effects(drug_names)
            result.common_side_effects = fallback.common_side_effects
            if not result.serious_side_effects:
                result.serious_side_effects = fallback.serious_side_effects
            if not result.warning_signs:
                result.warning_signs = fallback.warning_signs
            if not result.higher_risk_groups:
                result.higher_risk_groups = fallback.higher_risk_groups
            if not result.what_to_do:
                result.what_to_do = fallback.what_to_do

        logger.info("[Claude-SE] Side-effects explanation generated for %s", drug_names)
        return result

    except (json.JSONDecodeError, KeyError):
        logger.warning("[Claude-SE] JSON parse failed — using per-drug fallback")
        return _build_fallback_side_effects(drug_names)
    except Exception as exc:
        logger.warning("[Claude-SE] Failed: %s — using per-drug fallback", exc)
        return _build_fallback_side_effects(drug_names)


def _build_fallback(
    verdict: str,
    reasoning: str,
    drug_names: list[str],
    intent: str,
) -> Explanation:
    """
    Build a fallback explanation when Claude is unavailable.
    Dispatches to per-intent fallback builders.
    """
    # Side-effects intent gets a dedicated patient-friendly fallback
    if intent == "side_effects":
        return _build_fallback_side_effects(drug_names)

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
