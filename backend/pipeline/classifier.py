"""
Pipeline Step 1 — Fast Intent Classifier (zero AI calls).

Classifies a user query into one of 9 intent categories using keyword
matching only. Runs in <1 ms. No network calls, no LLM.

Intent categories:
  interaction          — "can I take X with Y", "X and Y together"
  dosing               — "how much", "what dose", "mg"
  side_effects         — "side effect", "adverse reaction"
  what_is              — "what is X", "what does X do"
  safety               — "is X safe", "can I take X"
  contraindications    — "who should not take", "avoid if"
  pregnancy_lactation  — "pregnant", "breastfeeding"
  food_alcohol         — "alcohol", "empty stomach", "with food"
  general              — fallback for everything else
"""
from __future__ import annotations

import re
from enum import Enum


# ── Regex patterns for typo-tolerant + causal side-effects detection ──────────
# These fire BEFORE string matching so typos ("effetcs") and causal patterns
# ("does metformin cause diarrhea") route correctly to SIDE_EFFECTS.

_SE_TYPO_RE = re.compile(
    r"\bside[\s\-]+eff?e[cft][cst]s?\b"   # effetcs, efects, effecst…
    r"|\bside[\s\-]+aff?ects?\b",           # side affects / side affect
    re.IGNORECASE,
)

_SE_CAUSAL_RE = re.compile(
    r"\b(?:does|can|will|could|might|may)\b.{1,60}\bcauses?\b"
    r"|\bis\b.{1,40}\ba\b.{0,20}\bside[\s\-]+effect\b",
    re.IGNORECASE,
)


class Intent(str, Enum):
    """9-type intent taxonomy. String enum so it serialises to JSON cleanly."""
    INTERACTION         = "interaction"
    DOSING              = "dosing"
    SIDE_EFFECTS        = "side_effects"
    WHAT_IS             = "what_is"
    SAFETY              = "safety"
    CONTRAINDICATIONS   = "contraindications"
    PREGNANCY_LACTATION = "pregnancy_lactation"
    FOOD_ALCOHOL        = "food_alcohol"
    GENERAL             = "general"


# ── Keyword tables ────────────────────────────────────────────────────────────
# Each tuple: (Intent, keywords). First match wins (priority order).
# SIDE_EFFECTS strong keywords always win over INTERACTION when ≤1 drug.
_SIDE_EFFECTS_STRONG: tuple[str, ...] = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "adverse reaction", "adverse reactions",
)

_INTERACTION_STRONG: frozenset[str] = frozenset({
    "interact", "interaction", "combine", "mix", "together",
})

_INTERACTION_WEAK: tuple[str, ...] = (
    "take with", "along with", "at the same time", "both", " and ", " with ",
)

_INTENT_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.WHAT_IS, (
        "what is", "what does", "is for what", "used for", "treat ",
        "prescribed for", "what condition", "tell me about",
        "what drug", "what medication", "what type of", "what kind of",
        "how does it work", "mechanism",
    )),
    (Intent.SIDE_EFFECTS, (
        "side effect", "side effects", "adverse effect", "adverse effects",
        "adverse reaction", "adverse reactions", "what does it cause",
        "what can it cause", "reaction to", "symptoms from",
    )),
    (Intent.DOSING, (
        "how much", "how many", "what dose", "dosage", "dosing", "dose",
        "how often", "how to take", "when to take", "mg ", "milligram",
        "maximum dose", "max dose", "daily dose", "strength", "twice a day",
        "once daily", "tablet", "capsule", "how long to take",
    )),
    (Intent.CONTRAINDICATIONS, (
        "contraindication", "can't take", "cannot take", "should not take",
        "not for", "avoid if", "allergic to", "allergy to",
        "who should not", "not safe for", "dangerous for",
    )),
    (Intent.PREGNANCY_LACTATION, (
        "pregnant", "pregnancy", "breastfeed", "trimester",
        "lactation", "while nursing", "fetus", "newborn", "expecting",
    )),
    (Intent.FOOD_ALCOHOL, (
        "alcohol", " drink ", "beer", "wine", "liquor", "grapefruit",
        "dairy", "with food", "empty stomach", "with meal", "without food",
        "with milk", "with water", "after eating", "before eating",
    )),
    (Intent.SAFETY, (
        "safe", "okay to", "can i take", "is it safe", "safely",
        "dangerous", "okay if", "alright to", "risk of taking",
    )),
]


# ── Emergency keywords — must short-circuit before any other classification ──
EMERGENCY_KEYWORDS: frozenset[str] = frozenset({
    "overdose", "overdosed", "took too many", "took too much",
    "can't breathe", "difficulty breathing", "not breathing",
    "unconscious", "unresponsive", "passed out", "chest pain",
    "heart attack", "stroke", "seizure", "seizures", "convulsions",
    "severe allergic", "anaphylaxis", "throat closing", "throat swelling",
    "suicidal", "suicide", "self-harm", "want to die", "kill myself",
    "severe bleeding", "coughing blood", "vomiting blood",
    "severe reaction",
})


def is_emergency(query: str) -> bool:
    """Return True if the query contains emergency / overdose keywords."""
    q = query.lower()
    return any(kw in q for kw in EMERGENCY_KEYWORDS)


def classify_fast(query: str, drug_count: int = 0) -> Intent:
    """
    Classify user query into an intent — zero AI, pure keyword matching.

    Priority:
      1. SIDE_EFFECTS (strong kw + ≤1 drug) beats INTERACTION
      2. INTERACTION (strong kw any count, or weak kw + 2+ drugs)
      3. All other intents in priority order
      4. GENERAL fallback

    Parameters
    ----------
    query      : raw user query string
    drug_count : number of drugs detected in the query (from drug extractor)

    Returns
    -------
    Intent enum value
    """
    q = query.lower()

    # Side effects beats interaction when only 1 drug present.
    # Regex check covers typos ("side effetcs") and causal patterns
    # ("does metformin cause diarrhea", "is cough a side effect of...").
    if drug_count <= 1 and (
        any(kw in q for kw in _SIDE_EFFECTS_STRONG)
        or _SE_TYPO_RE.search(q)
        or _SE_CAUSAL_RE.search(q)
    ):
        return Intent.SIDE_EFFECTS

    # Interaction: strong keywords (any drug count) or weak + 2+ drugs
    if any(kw in q for kw in _INTERACTION_STRONG):
        return Intent.INTERACTION
    if drug_count >= 2 and any(kw in q for kw in _INTERACTION_WEAK):
        return Intent.INTERACTION

    # Remaining intents in priority order
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in q for kw in keywords):
            return intent

    return Intent.GENERAL
