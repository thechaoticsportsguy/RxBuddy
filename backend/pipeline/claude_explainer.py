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
        "what_to_do": [
            "Take metformin with food to reduce stomach side effects",
            "Start with a low dose — your doctor will increase it gradually",
            "Get regular kidney tests as your doctor recommends",
            "Tell your doctor about any unusual weakness or stomach pain",
        ],
        "mechanism": "This drug helps the body use insulin better and lowers how much sugar the liver makes.",
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
        ],
        "what_to_do": [
            "The cough is a known, common side effect — talk to your doctor if it bothers you",
            "Rise slowly when standing up to prevent dizziness",
            "Monitor your blood pressure regularly",
        ],
        "mechanism": "This drug helps relax blood vessels so blood can flow more easily.",
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
        "what_to_do": [
            "Take folic acid supplements as directed — this significantly reduces side effects",
            "Avoid alcohol completely while on methotrexate",
            "Get all scheduled blood tests (liver, kidney, blood counts)",
            "Report any signs of infection immediately to your doctor",
        ],
        "mechanism": "This drug slows down the immune system and blocks cell growth to treat autoimmune conditions and cancer.",
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
            "Signs of low blood sugar: shakiness, sweating, confusion",
            "Lump or swelling in the neck",
        ],
        "what_to_do": [
            "Dose starts low and increases slowly — nausea usually fades after 4-8 weeks",
            "Eat smaller, lower-fat meals to reduce nausea",
            "Stay well hydrated, especially if experiencing vomiting or diarrhea",
            "Tell your doctor about any severe stomach pain or vomiting that won't stop",
        ],
        "mechanism": "This drug mimics GLP-1 hormone to regulate blood sugar and reduce appetite.",
    },
    # ── New drugs ──────────────────────────────────────────────────────────────
    "adderall": {
        "common": [
            "Decreased appetite",
            "Trouble sleeping",
            "Dry mouth",
            "Headache",
            "Increased heart rate",
            "Irritability",
        ],
        "serious": [
            "Heart problems",
            "High blood pressure",
            "Psychiatric symptoms",
            "Growth suppression in children",
        ],
        "warning_signs": [
            "Chest pain or shortness of breath",
            "New or worsening mood or behavior changes",
            "Signs of circulation problems: numbness, pain, or color changes in fingers or toes",
        ],
        "what_to_do": [
            "Take exactly as prescribed — do not increase your dose",
            "Report any chest pain, fast heartbeat, or mood changes to your doctor",
            "Avoid taking late in the day to prevent sleep problems",
        ],
        "mechanism": "This drug increases dopamine and norepinephrine in the brain to improve focus and reduce impulsivity.",
    },
    "risperidone": {
        "common": [
            "Drowsiness",
            "Weight gain",
            "Dizziness",
            "Constipation",
            "Dry mouth",
            "Increased appetite",
        ],
        "serious": [
            "Tardive dyskinesia",
            "High blood sugar",
            "Neuroleptic malignant syndrome",
            "QT prolongation",
        ],
        "warning_signs": [
            "Uncontrollable movements of the face, tongue, or jaw",
            "High fever with muscle stiffness and confusion",
            "Excessive thirst or frequent urination (may signal high blood sugar)",
        ],
        "what_to_do": [
            "Report any involuntary movements immediately to your doctor",
            "Have regular blood sugar monitoring",
            "Rise slowly from sitting or lying to prevent dizziness",
        ],
        "mechanism": "This drug blocks dopamine and serotonin receptors to reduce psychotic symptoms.",
    },
    "amlodipine": {
        "common": [
            "Swelling in ankles or feet",
            "Flushing",
            "Headache",
            "Dizziness",
            "Fatigue",
            "Nausea",
        ],
        "serious": [
            "Severe low blood pressure",
            "Worsening chest pain",
            "Heart attack in rare cases",
        ],
        "warning_signs": [
            "Severe dizziness or fainting",
            "Rapid or irregular heartbeat",
            "Worsening chest pain when starting or increasing dose",
        ],
        "what_to_do": [
            "Report any ankle swelling to your doctor",
            "Rise slowly from sitting to prevent dizziness",
            "Do not stop taking suddenly without talking to your doctor",
        ],
        "mechanism": "This drug relaxes blood vessels by blocking calcium channels, lowering blood pressure.",
    },
    "methocarbamol": {
        "common": [
            "Drowsiness",
            "Dizziness",
            "Nausea",
            "Blurred vision",
            "Headache",
        ],
        "serious": [
            "Seizures",
            "Allergic reaction",
            "Fainting",
        ],
        "warning_signs": [
            "Severe dizziness or fainting",
            "Seizures or convulsions",
            "Signs of allergic reaction: rash, swelling, difficulty breathing",
        ],
        "what_to_do": [
            "Do not drive or operate heavy machinery until you know how it affects you",
            "Avoid alcohol while taking this medication",
            "Tell your doctor if drowsiness becomes severe",
        ],
        "mechanism": "This drug relaxes muscles by depressing the central nervous system.",
    },
    "quetiapine": {
        "common": [
            "Drowsiness",
            "Dry mouth",
            "Dizziness",
            "Weight gain",
            "Constipation",
        ],
        "serious": [
            "Tardive dyskinesia",
            "High blood sugar",
            "Neuroleptic malignant syndrome",
        ],
        "warning_signs": [
            "Uncontrollable movements of the face, tongue, or jaw",
            "High fever with muscle stiffness",
            "Excessive thirst or frequent urination",
        ],
        "what_to_do": [
            "Have regular blood sugar and cholesterol monitoring",
            "Report any involuntary movements to your doctor",
            "Do not stop suddenly — taper under medical supervision",
        ],
        "mechanism": "This drug blocks multiple neurotransmitter receptors to treat psychosis and mood disorders.",
    },
    "gabapentin": {
        "common": [
            "Drowsiness",
            "Dizziness",
            "Fatigue",
            "Coordination problems",
            "Blurred vision",
        ],
        "serious": [
            "Respiratory depression",
            "Suicidal thoughts",
            "Severe allergic reaction",
        ],
        "warning_signs": [
            "Shallow or slow breathing",
            "New or worsening depression or suicidal thoughts",
            "Severe dizziness or drowsiness",
        ],
        "what_to_do": [
            "Do not stop suddenly — may cause withdrawal seizures",
            "Tell your doctor about any mood changes",
            "Avoid alcohol while taking this medication",
        ],
        "mechanism": "This drug reduces nerve signaling to control seizures and nerve pain.",
    },
    "omeprazole": {
        "common": [
            "Headache",
            "Nausea",
            "Diarrhea",
            "Stomach pain",
            "Constipation",
        ],
        "serious": [
            "Kidney problems",
            "Low magnesium",
            "C. diff infection",
            "Bone fractures with long-term use",
        ],
        "warning_signs": [
            "Severe diarrhea that does not improve",
            "Muscle spasms or irregular heartbeat (may signal low magnesium)",
            "Joint pain or bone pain",
        ],
        "what_to_do": [
            "Use the lowest dose for the shortest time needed",
            "Talk to your doctor about long-term use risks",
            "Report watery diarrhea or stomach pain that doesn't go away",
        ],
        "mechanism": "This drug reduces stomach acid by blocking the proton pump in stomach cells.",
    },
    "sertraline": {
        "common": [
            "Nausea",
            "Diarrhea",
            "Insomnia",
            "Dry mouth",
            "Sweating",
            "Sexual dysfunction",
        ],
        "serious": [
            "Suicidal thoughts in young adults",
            "Serotonin syndrome",
            "Bleeding risk",
        ],
        "warning_signs": [
            "New or worsening depression or suicidal thoughts",
            "Agitation, fever, fast heartbeat, muscle stiffness (serotonin syndrome)",
            "Unusual bleeding or bruising",
        ],
        "what_to_do": [
            "Do not stop suddenly — taper under medical supervision",
            "Report any mood changes or suicidal thoughts immediately",
            "Avoid alcohol while taking this medication",
        ],
        "mechanism": "This drug blocks serotonin reuptake in the brain to treat depression and anxiety.",
    },
    "escitalopram": {
        "common": [
            "Nausea",
            "Insomnia",
            "Sweating",
            "Fatigue",
            "Sexual dysfunction",
        ],
        "serious": [
            "Suicidal thoughts",
            "Serotonin syndrome",
            "QT prolongation",
        ],
        "warning_signs": [
            "New or worsening depression or suicidal thoughts",
            "Agitation, fever, fast heartbeat, muscle stiffness",
            "Fainting or irregular heartbeat",
        ],
        "what_to_do": [
            "Do not stop suddenly — taper under medical supervision",
            "Report any mood changes or suicidal thoughts immediately",
            "Avoid alcohol while taking this medication",
        ],
        "mechanism": "This drug selectively blocks serotonin reuptake to improve mood and reduce anxiety.",
    },
}

# Brand name aliases
for _alias, _generic in [
    ("ozempic", "semaglutide"), ("wegovy", "semaglutide"), ("rybelsus", "semaglutide"),
    ("glucophage", "metformin"), ("fortamet", "metformin"), ("glumetza", "metformin"),
    ("zestril", "lisinopril"), ("prinivil", "lisinopril"), ("qbrelis", "lisinopril"),
    ("rheumatrex", "methotrexate"), ("otrexup", "methotrexate"), ("rasuvo", "methotrexate"),
    # New aliases
    ("amphetamine", "adderall"), ("dextroamphetamine", "adderall"),
    ("risperdal", "risperidone"),
    ("norvasc", "amlodipine"),
    ("robaxin", "methocarbamol"),
    ("seroquel", "quetiapine"),
    ("neurontin", "gabapentin"),
    ("prilosec", "omeprazole"),
    ("zoloft", "sertraline"),
    ("lexapro", "escitalopram"),
]:
    _DRUG_SE_FALLBACK[_alias] = _DRUG_SE_FALLBACK[_generic]


# ── Banned generic phrases — these must never appear in side-effects bullets ───

_BANNED_SE_PHRASES = [
    "side effects vary",
    "consult your pharmacist for a complete list",
    "consult your pharmacist or prescriber for a complete list",
    "serious side effects are possible",
    "contact your provider if you experience unusual symptoms",
    "read the patient information leaflet",
]


def _has_banned_phrases(items: list[str]) -> bool:
    """Return True if any item in the list contains a banned generic phrase."""
    for item in items:
        lower = item.lower()
        for phrase in _BANNED_SE_PHRASES:
            if phrase in lower:
                return True
    return False


def _build_fallback_side_effects(
    drug_names: list[str], fda_labels: dict | None = None
) -> "Explanation":
    """Build a fallback side-effects explanation using hardcoded per-drug data or OpenFDA."""
    drug = drug_names[0].lower() if drug_names else ""
    data = _DRUG_SE_FALLBACK.get(drug)
    drugs_str = " and ".join(drug_names) if drug_names else "this medication"

    if data:
        return Explanation(
            answer=f"Here are the known side effects of {drugs_str}.",
            warning="If you experience severe or unusual symptoms, contact your healthcare provider.",
            details=[],
            action=data["what_to_do"][:3],
            article=data.get("mechanism", ""),
            common_side_effects=data["common"],
            serious_side_effects=data["serious"],
            warning_signs=data["warning_signs"],
            higher_risk_groups=[],
            what_to_do=data["what_to_do"],
            from_claude=False,
        )

    # ── OPENFDA FALLBACK ───────────────────────────────────────────────────────
    if fda_labels and drug in fda_labels:
        adverse_reactions = fda_labels[drug].get("adverse_reactions", "")
        if adverse_reactions:
            text = adverse_reactions.replace(";", ",")
            parts = [p.strip().lower() for p in text.split(",") if 3 < len(p.strip()) < 50]
            if parts:
                common_se = parts[:5]
                return Explanation(
                    answer=f"Here are the known side effects of {drugs_str} based on FDA label data.",
                    warning="Contact your healthcare provider if you experience severe or unusual symptoms.",
                    details=[],
                    action=[
                        "Ask your pharmacist for the full side effects list",
                        "Read the medication guide that comes with your prescription",
                    ],
                    article=f"{drugs_str.title()} is a prescription medication. Full mechanism details require clinical context.",
                    common_side_effects=common_se,
                    serious_side_effects=[],
                    warning_signs=[],
                    higher_risk_groups=[],
                    what_to_do=[],
                    from_claude=False,
                )

    # Generic fallback — drug not in our table and no FDA label available.
    return Explanation(
        answer=f"We don't have detailed side effect data for {drugs_str} yet. Please consult your pharmacist.",
        warning="Consult your pharmacist or prescriber about side effects.",
        details=[],
        action=[
            "Ask your pharmacist for the full side effects list",
            "Read the medication guide that comes with your prescription",
        ],
        article="",
        common_side_effects=[],
        serious_side_effects=[],
        warning_signs=[],
        higher_risk_groups=[],
        what_to_do=[],
        from_claude=False,
    )


# ── System prompts per intent ─────────────────────────────────────────────────
# Each prompt forces Claude to explain the GIVEN verdict, never override it.

_SIDE_EFFECTS_PROMPT = """You are a clinical pharmacist. The patient asked: '{query}'
The drug is: {drugs}

Return the ACTUAL known side effects of {drugs} specifically.
Do NOT return generic disclaimers.
Do NOT say "side effects vary".
Do NOT say "consult your pharmacist for a complete list".
Do NOT say "Serious side effects are possible".
Do NOT mention other drugs.

You MUST return real, specific, named side effects from FDA label data.

For {drugs}, list:
- common_side_effects: the 4-6 most frequently reported side effects
  (e.g. "nausea", "headache", "dizziness", "dry mouth")
- serious_side_effects: 2-4 serious or rare but important side effects
  (e.g. "liver damage", "severe allergic reaction", "QT prolongation")
- mechanism_simple: one plain-English sentence explaining how {drugs} works
  (e.g. "This drug blocks serotonin reuptake to improve mood.")

Use simple everyday language. Translate medical jargon:
  somnolence -> "feeling drowsy", pruritus -> "itching", edema -> "swelling",
  dyspepsia -> "stomach upset", myalgia -> "muscle pain"

Return ONLY this JSON, no other text:
{{"common_side_effects": ["side effect 1", "side effect 2", "side effect 3", "side effect 4"],
  "serious_side_effects": ["serious effect 1", "serious effect 2"],
  "mechanism_simple": "one sentence plain English explanation",
  "short_answer": "one sentence summary of side effect profile",
  "when_to_get_help": ["warning sign 1", "warning sign 2"],
  "confidence": "HIGH"}}

EVIDENCE:
{context}"""

_SIDE_EFFECTS_RETRY_PROMPT = """RETRY: Your previous response contained generic disclaimers.
Do NOT use generic phrases. List SPECIFIC NAMED side effects only.
For {drugs}, return ONLY real, specific side effects like "nausea", "headache", "dizziness".
Never say "side effects vary" or "consult your pharmacist for a complete list".

Return ONLY valid JSON:
{{"common_side_effects": ["specific effect 1", "specific effect 2", "specific effect 3", "specific effect 4"],
  "serious_side_effects": ["specific serious effect 1", "specific serious effect 2"],
  "mechanism_simple": "one sentence how the drug works",
  "short_answer": "one sentence summary",
  "when_to_get_help": ["warning sign 1", "warning sign 2"],
  "confidence": "HIGH"}}

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
    Uses a strict prompt that demands specific named effects.
    Validates response against banned generic phrases and retries if needed.
    """
    if not api_key:
        logger.warning("[Claude-SE] No API key — using per-drug fallback")
        return _build_fallback_side_effects(drug_names, fda_labels)

    context = _build_side_effects_context(
        drug_names=drug_names,
        verdict=verdict,
        reasoning=reasoning,
        fda_labels=fda_labels,
        adverse_events=adverse_events,
    )

    drugs_str = " and ".join(drug_names) if drug_names else "this medication"
    max_retries = 2

    for attempt in range(max_retries + 1):
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=10.0)

            # Use retry prompt on subsequent attempts
            if attempt == 0:
                system_prompt = _SIDE_EFFECTS_PROMPT.format(
                    drugs=", ".join(drug_names),
                    query=query,
                    context=context,
                )
            else:
                system_prompt = _SIDE_EFFECTS_RETRY_PROMPT.format(
                    drugs=", ".join(drug_names),
                    context=context,
                )

            user_message = f"Question: {query}\n\nList the specific, named side effects of {', '.join(drug_names)}."

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

            common = list(parsed.get("common_side_effects", []))[:6]
            serious = list(parsed.get("serious_side_effects", []))[:4]
            mechanism = str(parsed.get("mechanism_simple", ""))[:300]
            short_answer = str(parsed.get("short_answer", ""))[:300]
            when_to_get_help = list(parsed.get("when_to_get_help", parsed.get("warning_signs", [])))[:4]

            # ── Validate: reject generic disclaimers ──────────────────────
            if len(common) < 3 or _has_banned_phrases(common) or _has_banned_phrases(serious):
                if attempt < max_retries:
                    logger.warning(
                        "[Claude-SE] Attempt %d returned generic/empty — retrying",
                        attempt + 1,
                    )
                    continue
                # Max retries exhausted — fall through to fallback
                logger.warning("[Claude-SE] All retries exhausted — using fallback")
                return _build_fallback_side_effects(drug_names, fda_labels)

            result = Explanation(
                answer=short_answer or f"Here are the known side effects of {drugs_str}.",
                warning="Contact your healthcare provider if you experience severe or unusual symptoms.",
                details=[],
                action=[],
                article=mechanism,
                common_side_effects=common,
                serious_side_effects=serious,
                warning_signs=when_to_get_help,
                higher_risk_groups=[],
                what_to_do=[],
                from_claude=True,
            )

            # Guard: backfill from fallback table if empty
            if not result.common_side_effects:
                fallback = _build_fallback_side_effects(drug_names, fda_labels)
                result.common_side_effects = fallback.common_side_effects
                if not result.serious_side_effects:
                    result.serious_side_effects = fallback.serious_side_effects
                if not result.warning_signs:
                    result.warning_signs = fallback.warning_signs

            logger.info("[Claude-SE] Side-effects generated for %s (attempt %d)", drug_names, attempt + 1)
            return result

        except (json.JSONDecodeError, KeyError) as exc:
            if attempt < max_retries:
                logger.warning("[Claude-SE] JSON parse failed (attempt %d) — retrying", attempt + 1)
                continue
            logger.warning("[Claude-SE] JSON parse failed after retries — using fallback")
            return _build_fallback_side_effects(drug_names, fda_labels)
        except Exception as exc:
            logger.warning("[Claude-SE] Failed: %s — using per-drug fallback", exc)
            return _build_fallback_side_effects(drug_names, fda_labels)

    # Should never reach here, but just in case
    return _build_fallback_side_effects(drug_names, fda_labels)


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
