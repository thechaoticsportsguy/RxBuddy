"""
RxBuddy Answer Engine — v2
==========================

SYSTEM ARCHITECTURE
───────────────────

                        User Query
                             │
                  ┌──────────▼──────────┐
                  │  1. Query Normalizer │  slang→medical, typo fix
                  └──────────┬──────────┘
                             │
                  ┌──────────▼──────────┐
                  │  2. IntentClassifier │  7 types (see QuestionIntent)
                  └──────────┬──────────┘
                             │
                  ┌──────────▼──────────┐
                  │  3. Drug Extractor   │  RxNorm → RxCUI normalization
                  └──────┬──────────────┘
                         │
          ┌──────────────┼──────────────────┐
          │              │                  │
   ┌──────▼───┐   ┌──────▼──────┐   ┌──────▼──────┐
   │4a. TF-IDF│   │ 4b. FDA     │   │ 4c. PubMed  │
   │  DB Q&A  │   │  Labels     │   │  Search     │
   └──────┬───┘   │ (OpenFDA +  │   │ (supplement │
          │       │  DailyMed)  │   │  only)      │
          │       └──────┬──────┘   └──────┬──────┘
          │              │                  │
          └──────────────▼──────────────────┘
                         │
              ┌──────────▼──────────┐
              │ 5. Retrieval Guard  │  "No source → no answer"
              │                    │  for DOSING / CONTRAINDICATIONS /
              │                    │  PREGNANCY / HIGH-RISK INTERACTIONS
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │ 6. Answer Generator │  Claude Sonnet, grounded context
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │ 7. Validator        │  Claude Haiku, verdict consistency
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │ 8. Citation Assembler│  SetID + NDA + revision date + URL
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │ 9. RxBuddyAnswer    │  Full JSON → API → UI cards
              └─────────────────────┘

DATA SOURCES AND HIERARCHY
──────────────────────────
  1. Drugs@FDA    – most recent FDA-approved prescribing information (updated daily)
     URL:  https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-database
  2. DailyMed     – "in use" SPL labeling; may lag Drugs@FDA; NLM does not review content
     URL:  https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm
  3. PubMed       – primary literature (supplement only; never sole source for dosing/CIs)
     URL:  https://pubmed.ncbi.nlm.nih.gov/about/
  4. RxNorm       – drug name normalization + RxCUI lookup (NLM attribution required)
     URL:  https://www.nlm.nih.gov/research/umls/rxnorm/docs/termsofservice.html

RETRIEVAL RULES BY INTENT
──────────────────────────
  INTERACTION         → drug_interactions, warnings, boxed_warning        | PubMed OK (supplement)
  DOSING              → dosage_and_administration                          | NO PubMed; REFUSE if no label
  SIDE_EFFECTS        → adverse_reactions, warnings, boxed_warning        | PubMed OK
  CONTRAINDICATIONS   → contraindications, warnings, boxed_warning        | NO PubMed; REFUSE if no label
  PREGNANCY_LACTATION → pregnancy, lactation, use_in_specific_populations | NO PubMed; REFUSE if no label
  FOOD_ALCOHOL        → drug_interactions (food subsect.), warnings        | PubMed OK
  GENERAL             → indications_and_usage, warnings                   | PubMed OK

"NO RETRIEVAL, NO ANSWER" RULE
───────────────────────────────
  If intent ∈ {DOSING, CONTRAINDICATIONS, PREGNANCY_LACTATION}:
    → If FDA label not retrieved → return INSUFFICIENT_DATA (do not call Claude)
  If intent == INTERACTION and any drug is a narrow-therapeutic-index / high-risk drug:
    → Same refusal rule applies
  For all other intents: answer with available context + lower confidence rating

LABEL UPDATE PIPELINE  (see label_updater.py)
──────────────────────
  • TTL-based in-process cache (24 h default)
  • refresh_stale_labels() callable by cron job
  • effective_time field tracks label revision date (YYYYMMDD → ISO)
  • DailyMed vs Drugs@FDA divergence disclosed via source field in Citation

CITATION OBJECT FORMAT
───────────────────────
  {
    "id":                  "cit_0",
    "source":              "DailyMed" | "Drugs@FDA" | "PubMed" | "RxNorm",
    "source_url":          "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=...",
    "section":             "drug_interactions",
    "section_label":       "Drug Interactions",
    "drug_name":           "metformin",
    "set_id":              "uuid-from-openfda",
    "nda_number":          "NDA021202",
    "label_revision_date": "2023-04-15",
    "date_fetched":        "2026-03-19T10:00:00Z"
  }

ANSWER JSON SCHEMA  (RxBuddyAnswer)
────────────────────
  {
    "verdict":              "CAUTION",
    "intent":               "interaction",
    "retrieval_status":     "LABEL_FOUND",
    "confidence":           "HIGH",
    "short_answer":         "Use with caution ...",
    "rationale":            [{"point": "...", "citation_ref": "cit_0"}],
    "what_to_do":           ["...", "..."],
    "emergency_escalation": ["...", "..."],
    "citations":            [ <Citation objects> ],
    "last_reviewed":        "2026-03-19T10:00:00Z"
  }
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

_logger = logging.getLogger("rxbuddy.engine")


# ── 1. INTENT CLASSIFIER ──────────────────────────────────────────────────────

class QuestionIntent(str, Enum):
    """
    9-type question intent taxonomy.
    Used for retrieval routing, citation section selection, and guard logic.
    """
    INTERACTION         = "interaction"
    WHAT_IS             = "what_is"
    SIDE_EFFECTS        = "side_effects"
    DOSING              = "dosing"
    SAFETY              = "safety"
    CONTRAINDICATIONS   = "contraindications"
    PREGNANCY_LACTATION = "pregnancy_lactation"
    FOOD_ALCOHOL        = "food_alcohol"
    GENERAL             = "general"


# Strong interaction keywords — trigger INTERACTION even with a single drug
_INTERACTION_STRONG_KW: frozenset[str] = frozenset({
    "interact", "interaction", "combine", "mix", "together",
})

# Weak interaction keywords — only trigger INTERACTION when 2+ drugs are present
_INTERACTION_WEAK_KW: tuple[str, ...] = (
    "take with", "along with", "at the same time", "both", " and ", " with ",
)

# Priority-ordered keyword fingerprints for all NON-INTERACTION intents
_INTENT_KEYWORDS: list[tuple[QuestionIntent, tuple[str, ...]]] = [
    (QuestionIntent.WHAT_IS, (
        "what is", "what does", "is for what", "used for", "treat ",
        "prescribed for", "what condition", "tell me about",
        "what drug", "what medication", "what type of", "what kind of",
        "how does it work", "mechanism",
    )),
    (QuestionIntent.SIDE_EFFECTS, (
        "side effect", "side effects", "adverse effect", "adverse effects",
        "adverse reaction", "adverse reactions", "what does it cause",
        "what can it cause", "reaction to", "symptoms from",
    )),
    (QuestionIntent.DOSING, (
        "how much", "how many", "what dose", "dosage", "dosing", "dose",
        "how often", "how to take", "when to take", "mg ", "milligram",
        "maximum dose", "max dose", "daily dose", "strength", "twice a day",
        "once daily", "tablet", "capsule", "how long to take",
    )),
    (QuestionIntent.SAFETY, (
        "safe", "okay to", "can i take", "is it safe", "safely",
        "while pregnant", "breastfeeding", "nursing", "during pregnancy",
        "dangerous", "okay if", "alright to", "risk of taking",
    )),
    (QuestionIntent.CONTRAINDICATIONS, (
        "contraindication", "can't take", "cannot take", "should not take",
        "not for", "avoid if", "allergic to", "allergy to",
        "who should not", "not safe for", "dangerous for",
        "do i have to stop", "need to stop",
    )),
    (QuestionIntent.PREGNANCY_LACTATION, (
        "pregnant", "pregnancy", "breastfeed", "trimester",
        "lactation", "while nursing", "fetus", "newborn", "expecting",
    )),
    (QuestionIntent.FOOD_ALCOHOL, (
        "alcohol", " drink ", "beer", "wine", "liquor", "grapefruit",
        "dairy", "with food", "empty stomach", "with meal", "without food",
        "with milk", "with water", "after eating", "before eating",
    )),
]


_SIDE_EFFECTS_STRONG_KW: tuple[str, ...] = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "adverse reaction", "adverse reactions",
)


def classify_intent(question: str, drug_count: int = 0) -> QuestionIntent:
    """
    Classify a user question into one of 9 intents.

    Priority order (first match wins):
      SIDE_EFFECTS (when drug_count ≤ 1 + SE keyword) >
      INTERACTION (2+ drugs OR strong kw) >
      WHAT_IS > SIDE_EFFECTS > DOSING > SAFETY > CONTRAINDICATIONS >
      PREGNANCY_LACTATION > FOOD_ALCOHOL > GENERAL

    Key rule: SIDE_EFFECTS always beats INTERACTION when only one drug is
    present. "duloxetine side effects" has 1 drug → SIDE_EFFECTS, period.

    Examples
    --------
    "duloxetine side effects"             → SIDE_EFFECTS  (1 drug)
    "side effects of ibuprofen"           → SIDE_EFFECTS  (1 drug)
    "can I take ibuprofen with warfarin?" → INTERACTION   (2 drugs + 'with')
    "what is metformin used for?"         → WHAT_IS
    "lisinopril is for what"              → WHAT_IS
    "how much ibuprofen can I take?"      → DOSING
    "is sertraline safe during pregnancy?"→ SAFETY
    "warfarin contraindications"          → CONTRAINDICATIONS
    """
    q = question.lower()

    # ── SIDE_EFFECTS beats INTERACTION when only 1 drug is in the query ───────
    # This prevents MAOI interaction warnings from bleeding into side-effect answers.
    if drug_count <= 1 and any(kw in q for kw in _SIDE_EFFECTS_STRONG_KW):
        return QuestionIntent.SIDE_EFFECTS

    # ── INTERACTION: strong keywords (no drug count required) OR weak keywords
    #    with 2+ drugs detected
    if any(kw in q for kw in _INTERACTION_STRONG_KW):
        return QuestionIntent.INTERACTION
    if drug_count >= 2 and any(kw in q for kw in _INTERACTION_WEAK_KW):
        return QuestionIntent.INTERACTION

    # ── Remaining intents in priority order ───────────────────────────────────
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in q for kw in keywords):
            return intent

    return QuestionIntent.GENERAL


# ── 2. RETRIEVAL RULES ────────────────────────────────────────────────────────

# Intent → ordered list of FDA label sections to retrieve
RETRIEVAL_SECTIONS: dict[QuestionIntent, list[str]] = {
    QuestionIntent.INTERACTION:         ["drug_interactions", "warnings", "boxed_warning"],
    QuestionIntent.WHAT_IS:             ["indications_and_usage", "clinical_pharmacology", "description"],
    QuestionIntent.SIDE_EFFECTS:        ["adverse_reactions", "warnings", "boxed_warning"],
    QuestionIntent.DOSING:              ["dosage_and_administration"],
    QuestionIntent.SAFETY:              ["warnings", "contraindications", "boxed_warning"],
    QuestionIntent.CONTRAINDICATIONS:   ["contraindications", "warnings", "boxed_warning"],
    QuestionIntent.PREGNANCY_LACTATION: ["pregnancy", "lactation", "use_in_specific_populations"],
    QuestionIntent.FOOD_ALCOHOL:        ["drug_interactions", "warnings"],
    QuestionIntent.GENERAL:             ["indications_and_usage", "warnings"],
}

# Intents that REQUIRE a label — refuse without one
_NO_SOURCE_REFUSE_INTENTS: frozenset[QuestionIntent] = frozenset({
    QuestionIntent.DOSING,
    QuestionIntent.CONTRAINDICATIONS,
    QuestionIntent.PREGNANCY_LACTATION,
})

# High-risk drugs that also require a label for interaction queries
# (narrow therapeutic index or high-toxicity agents)
HIGH_RISK_DRUGS: frozenset[str] = frozenset({
    # Anticoagulants / antiplatelets
    "warfarin", "heparin", "enoxaparin", "clopidogrel",
    "apixaban", "rivaroxaban", "dabigatran", "edoxaban",
    "prasugrel", "ticagrelor",
    # Antiarrhythmics
    "digoxin", "amiodarone", "sotalol", "flecainide", "dronedarone",
    # Narrow therapeutic index AEDs / mood stabilisers
    "lithium", "phenytoin", "carbamazepine", "valproate", "valproic acid",
    "phenobarbital",
    # Immunosuppressants
    "methotrexate", "cyclosporine", "tacrolimus", "mycophenolate",
    # Other narrow TI
    "theophylline", "digoxin", "vancomycin", "linezolid",
    # High-risk opioids
    "fentanyl", "methadone", "buprenorphine",
    # Oncology
    "tamoxifen", "isotretinoin", "methotrexate",
    # Antidepressants with narrow TI / serotonin risk
    "amitriptyline", "clomipramine", "imipramine", "nortriptyline",
    # Antipsychotics — clozapine requires strict monitoring
    "clozapine", "haloperidol",
})

# Known high-risk drug PAIRS — any combination → always CAUTION/AVOID regardless of label
# Format: frozenset of two lowercase drug names
HIGH_RISK_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"warfarin", "aspirin"}),           # major bleeding
    frozenset({"warfarin", "ibuprofen"}),         # major bleeding
    frozenset({"warfarin", "naproxen"}),          # major bleeding
    frozenset({"warfarin", "diclofenac"}),        # major bleeding
    frozenset({"warfarin", "celecoxib"}),         # moderate bleeding
    frozenset({"apixaban", "aspirin"}),           # increased bleeding
    frozenset({"apixaban", "ibuprofen"}),         # increased bleeding
    frozenset({"rivaroxaban", "aspirin"}),        # increased bleeding
    frozenset({"rivaroxaban", "ibuprofen"}),      # increased bleeding
    frozenset({"clopidogrel", "aspirin"}),        # dual antiplatelet — monitor
    frozenset({"clopidogrel", "omeprazole"}),     # CYP2C19 — reduced efficacy
    frozenset({"methotrexate", "ibuprofen"}),     # methotrexate toxicity
    frozenset({"methotrexate", "naproxen"}),      # methotrexate toxicity
    frozenset({"lithium", "ibuprofen"}),          # lithium toxicity
    frozenset({"lithium", "naproxen"}),           # lithium toxicity
    frozenset({"lithium", "diclofenac"}),         # lithium toxicity
    frozenset({"digoxin", "amiodarone"}),         # digoxin toxicity
    frozenset({"digoxin", "verapamil"}),          # digoxin toxicity
    frozenset({"digoxin", "diltiazem"}),          # digoxin toxicity
    frozenset({"sotalol", "amiodarone"}),         # QT prolongation / fatal arrhythmia
    frozenset({"haloperidol", "amiodarone"}),     # QT prolongation
    frozenset({"sildenafil", "nitroglycerin"}),   # severe hypotension / fatal
    frozenset({"tadalafil", "nitroglycerin"}),    # severe hypotension / fatal
    frozenset({"sildenafil", "isosorbide mononitrate"}),  # severe hypotension
    frozenset({"sertraline", "tramadol"}),        # serotonin syndrome
    frozenset({"fluoxetine", "tramadol"}),        # serotonin syndrome
    frozenset({"linezolid", "sertraline"}),       # serotonin syndrome / MAOI effect
    frozenset({"linezolid", "fluoxetine"}),       # serotonin syndrome
    frozenset({"metformin", "ibuprofen"}),        # kidney strain / lactic acidosis risk
    frozenset({"metformin", "naproxen"}),         # kidney strain
    frozenset({"lisinopril", "ibuprofen"}),       # reduced antihypertensive effect + AKI
    frozenset({"lisinopril", "naproxen"}),        # AKI triple whammy risk
    frozenset({"lisinopril", "potassium chloride"}), # hyperkalemia
    frozenset({"lisinopril", "spironolactone"}),  # hyperkalemia
    frozenset({"isotretinoin", "tetracycline"}),  # pseudotumor cerebri
    frozenset({"isotretinoin", "doxycycline"}),   # pseudotumor cerebri
    frozenset({"isotretinoin", "minocycline"}),   # pseudotumor cerebri
})

# Emergency / overdose keywords → always escalate to emergency services
_EMERGENCY_KEYWORDS: frozenset[str] = frozenset({
    "overdose", "overdosed", "took too many", "took too much",
    "can't breathe", "can't breathing", "difficulty breathing", "not breathing",
    "unconscious", "unresponsive", "passed out", "chest pain", "chest tightness",
    "heart attack", "stroke", "seizure", "seizures", "convulsions",
    "severe allergic", "anaphylaxis", "throat closing", "throat swelling",
    "suicidal", "suicide", "self-harm", "want to die", "kill myself",
    "severe bleeding", "coughing blood", "vomiting blood", "blood in stool",
    "severe reaction",
})


def detect_emergency(query: str) -> bool:
    """
    Return True if the query contains emergency / overdose keywords.
    When True, the answer must escalate to emergency services immediately.
    """
    q = query.lower()
    return any(kw in q for kw in _EMERGENCY_KEYWORDS)


def check_high_risk_pair(drug_names: list[str]) -> Optional[tuple[str, str]]:
    """
    Return (drug_a, drug_b) if the combination is in HIGH_RISK_PAIRS, else None.
    Used to upgrade verdict to CAUTION/AVOID regardless of label content.
    """
    lower = [d.lower() for d in drug_names]
    for i, a in enumerate(lower):
        for b in lower[i + 1:]:
            if frozenset({a, b}) in HIGH_RISK_PAIRS:
                return (a, b)
    return None


class RetrievalStatus(str, Enum):
    LABEL_FOUND       = "LABEL_FOUND"
    LABEL_NOT_FOUND   = "LABEL_NOT_FOUND"
    REFUSED_NO_SOURCE = "REFUSED_NO_SOURCE"
    PUBMED_ONLY       = "PUBMED_ONLY"


def check_retrieval_guard(
    intent: QuestionIntent,
    fda_data: "dict | None",
    drug_names: "list[str]",
    query: str = "",
) -> "tuple[bool, RetrievalStatus]":
    """
    Enforce the 'no retrieval, no answer' rule.

    Returns (proceed: bool, status: RetrievalStatus).
    If proceed=False, caller must return a REFUSED/INSUFFICIENT_DATA answer
    without calling Claude.

    Rules (in priority order)
    -------------------------
    1. Emergency keywords (overdose, can't breathe, etc.)  → REFUSE always
    2. DOSING / CONTRAINDICATIONS / PREGNANCY_LACTATION    → REFUSE if no label
    3. INTERACTION with a high-risk drug                   → REFUSE if no label
    4. Known HIGH_RISK_PAIR combination                    → proceed but flag
    5. All other intents                                   → proceed (lower confidence)
    """
    has_label = bool(fda_data)

    # Rule 1 — emergency queries must not get an automated answer
    if query and detect_emergency(query):
        return False, RetrievalStatus.REFUSED_NO_SOURCE

    # Rule 2 — high-stakes intents need a label
    if intent in _NO_SOURCE_REFUSE_INTENTS and not has_label:
        return False, RetrievalStatus.REFUSED_NO_SOURCE

    # Rule 3 — interactions involving narrow-TI drugs need a label
    if intent == QuestionIntent.INTERACTION:
        involves_high_risk = any(d.lower() in HIGH_RISK_DRUGS for d in drug_names)
        if involves_high_risk and not has_label:
            return False, RetrievalStatus.REFUSED_NO_SOURCE

    if has_label:
        return True, RetrievalStatus.LABEL_FOUND

    return True, RetrievalStatus.LABEL_NOT_FOUND


# ── 3. CITATION MODELS ────────────────────────────────────────────────────────

class Citation(BaseModel):
    """
    A single verifiable source citation attached to a medical claim.
    Every claim in RxBuddyAnswer.rationale links to a Citation via citation_ref.
    """
    id: str                                  # e.g. "cit_0"
    source: str                              # "DailyMed" | "Drugs@FDA" | "PubMed" | "RxNorm"
    source_url: str                          # Direct link to the source document
    section: str                             # Raw FDA section key e.g. "drug_interactions"
    section_label: str                       # Human-readable e.g. "Drug Interactions"
    drug_name: str                           # Normalised generic name
    set_id: Optional[str] = None            # DailyMed/OpenFDA SetID (UUID)
    nda_number: Optional[str] = None        # e.g. "NDA021202" or "BLA125057"
    label_revision_date: Optional[str] = None  # ISO date e.g. "2023-04-15"
    date_fetched: str                        # ISO 8601 UTC e.g. "2026-03-19T10:00:00Z"

    # DailyMed may lag Drugs@FDA — disclose divergence when set_id present
    @property
    def source_note(self) -> str:
        if self.set_id:
            return (
                "Source: DailyMed (NLM/NIH). "
                "In-use SPL labeling may differ from the most recent FDA-approved labeling "
                "at Drugs@FDA. NLM does not review SPL content prior to publication."
            )
        return "Source: Drugs@FDA (FDA). Updated daily with most recent approved labeling."


class Rationale(BaseModel):
    """A single grounded rationale bullet with its citation reference."""
    point: str         # The medical claim text
    citation_ref: str  # e.g. "cit_0" — references Citation.id


class RxBuddyAnswer(BaseModel):
    """
    Full structured answer schema for the RxBuddy answer engine.

    Every medical claim in `rationale` is grounded in a `Citation` object.
    Compatible with UI card rendering.

    Verdict values
    --------------
    SAFE              – no meaningful risk from label evidence
    CAUTION           – moderate risk; monitoring or dose adjustment needed
    AVOID             – high risk, contraindicated, or major interaction
    CONSULT_CLINICIAN – personalised guidance required; cannot answer from label alone
    INSUFFICIENT_DATA – retrieval guard fired; refused without label source
    """
    verdict: str               # SAFE | CAUTION | AVOID | CONSULT_CLINICIAN | INSUFFICIENT_DATA
    intent: str                # QuestionIntent value
    retrieval_status: str      # RetrievalStatus value
    confidence: str            # HIGH | MEDIUM | LOW
    short_answer: str          # One plain-English sentence
    rationale: list[Rationale] = Field(default_factory=list)
    what_to_do: list[str] = Field(default_factory=list)
    emergency_escalation: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    last_reviewed: str         # ISO 8601 UTC timestamp


# ── 4. FDA METADATA EXTRACTION ────────────────────────────────────────────────

_SECTION_LABELS: dict[str, str] = {
    "drug_interactions":            "Drug Interactions",
    "dosage_and_administration":    "Dosage and Administration",
    "adverse_reactions":            "Adverse Reactions",
    "contraindications":            "Contraindications",
    "warnings":                     "Warnings and Precautions",
    "boxed_warning":                "Boxed Warning (Black Box)",
    "pregnancy":                    "Pregnancy",
    "lactation":                    "Lactation",
    "use_in_specific_populations":  "Use in Specific Populations",
    "indications_and_usage":        "Indications and Usage",
}

DAILYMED_SETID_URL  = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}"
DRUGSFDA_SEARCH_URL = "https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-database"


def extract_fda_metadata(raw_openfda_label: "dict | None") -> dict:
    """
    Extract SetID, NDA/BLA number, and label revision date from a raw OpenFDA label result.

    The OpenFDA label endpoint returns results[0] with:
      - openfda.set_id          → DailyMed SetID (UUID list)
      - openfda.application_number → NDA/BLA number list
      - effective_time          → YYYYMMDD string (label revision date)

    Returns a dict with keys: set_id, nda_number, label_revision_date, source_url.
    All fields may be None if the OpenFDA response omits them.
    """
    if not raw_openfda_label:
        return {"set_id": None, "nda_number": None, "label_revision_date": None,
                "source_url": DRUGSFDA_SEARCH_URL}

    openfda = raw_openfda_label.get("openfda", {})

    set_ids = openfda.get("set_id", [])
    set_id = set_ids[0] if set_ids else None

    app_numbers = openfda.get("application_number", [])
    nda_number = app_numbers[0] if app_numbers else None

    # effective_time format: "20230415" → "2023-04-15"
    effective_time = str(raw_openfda_label.get("effective_time", "") or "")
    label_revision_date: Optional[str] = None
    if len(effective_time) == 8 and effective_time.isdigit():
        try:
            label_revision_date = f"{effective_time[:4]}-{effective_time[4:6]}-{effective_time[6:8]}"
        except Exception:
            pass

    source_url = DAILYMED_SETID_URL.format(set_id=set_id) if set_id else DRUGSFDA_SEARCH_URL

    return {
        "set_id": set_id,
        "nda_number": nda_number,
        "label_revision_date": label_revision_date,
        "source_url": source_url,
    }


def build_citations(
    fda_data: "dict | None",
    raw_label: "dict | None",
    drug_name: str,
    intent: QuestionIntent,
    fetched_at: str,
) -> "list[Citation]":
    """
    Build Citation objects from the FDA data we fetched for this query.

    Only creates citations for sections that actually have content.
    Attaches SetID, NDA number, and label revision date from raw OpenFDA metadata.
    """
    if not fda_data:
        return []

    metadata = extract_fda_metadata(raw_label)
    sections = RETRIEVAL_SECTIONS.get(intent, [])

    citations: list[Citation] = []
    for idx, section_key in enumerate(sections):
        if not fda_data.get(section_key):
            continue

        set_id = metadata.get("set_id")
        citations.append(Citation(
            id=f"cit_{idx}",
            source="DailyMed" if set_id else "Drugs@FDA",
            source_url=metadata.get("source_url", DRUGSFDA_SEARCH_URL),
            section=section_key,
            section_label=_SECTION_LABELS.get(
                section_key,
                section_key.replace("_", " ").title()
            ),
            drug_name=drug_name or "",
            set_id=set_id,
            nda_number=metadata.get("nda_number"),
            label_revision_date=metadata.get("label_revision_date"),
            date_fetched=fetched_at,
        ))

    # Ensure at least one citation exists when we have label data
    if not citations and fda_data:
        metadata_su = metadata.get("source_url", DRUGSFDA_SEARCH_URL)
        citations.append(Citation(
            id="cit_0",
            source="Drugs@FDA",
            source_url=metadata_su,
            section="general",
            section_label="Drug Label",
            drug_name=drug_name or "",
            set_id=metadata.get("set_id"),
            nda_number=metadata.get("nda_number"),
            label_revision_date=metadata.get("label_revision_date"),
            date_fetched=fetched_at,
        ))

    return citations


# ── 5. REFUSED ANSWER BUILDER ─────────────────────────────────────────────────

_REFUSED_MESSAGES: dict[QuestionIntent, str] = {
    QuestionIntent.WHAT_IS: (
        "Drug information requires the official drug label, which could not be retrieved. "
        "Please check DailyMed (dailymed.nlm.nih.gov) or consult a pharmacist."
    ),
    QuestionIntent.SAFETY: (
        "Safety information requires the official drug label, which could not be retrieved. "
        "Please check DailyMed or consult a healthcare provider before taking this medication."
    ),
    QuestionIntent.DOSING: (
        "Dosing information requires the official drug label, which could not be retrieved "
        "for this medication. Please consult a licensed pharmacist or check the official "
        "prescribing information at DailyMed (dailymed.nlm.nih.gov) for accurate dosing guidance."
    ),
    QuestionIntent.CONTRAINDICATIONS: (
        "Contraindication data requires the official drug label, which could not be retrieved. "
        "Please check the official prescribing information at DailyMed or consult a healthcare provider."
    ),
    QuestionIntent.PREGNANCY_LACTATION: (
        "Pregnancy and lactation safety data requires the official drug label, which could not "
        "be retrieved. Please consult your OB/GYN or pharmacist and review the official "
        "prescribing information before taking any medication during pregnancy or while breastfeeding."
    ),
    QuestionIntent.INTERACTION: (
        "Interaction data for one or more medications in your question could not be retrieved "
        "from the official drug label. Please consult a pharmacist or review the official "
        "labels at DailyMed before combining these medications."
    ),
}

# Message used when emergency keywords are detected
_EMERGENCY_REFUSED_MESSAGE = (
    "This appears to describe a medical emergency. "
    "RxBuddy cannot provide emergency guidance — please call 911 (US) or your local emergency "
    "number immediately, or contact Poison Control at 1-800-222-1222 (US)."
)

# Message used when a drug is not recognised in any known source
_UNKNOWN_DRUG_MESSAGE = (
    "The medication name could not be matched to an official drug record. "
    "This may be a very rare drug, a misspelling, or a regional brand name. "
    "Please verify the name and consult a licensed pharmacist or healthcare provider."
)


def build_emergency_answer(fetched_at: str) -> "RxBuddyAnswer":
    """Return a hard-coded emergency escalation response (no Claude call)."""
    return RxBuddyAnswer(
        verdict="EMERGENCY",
        intent=QuestionIntent.GENERAL.value,
        retrieval_status=RetrievalStatus.REFUSED_NO_SOURCE.value,
        confidence="HIGH",
        short_answer=_EMERGENCY_REFUSED_MESSAGE,
        rationale=[],
        what_to_do=[],
        emergency_escalation=[
            "Call 911 (US) or your local emergency number immediately.",
            "Contact Poison Control: 1-800-222-1222 (US) or visit poisoncontrol.org.",
            "Do not wait — go to the nearest emergency room if needed.",
        ],
        citations=[],
        last_reviewed=fetched_at,
    )


def build_unknown_drug_answer(drug_name: str, fetched_at: str) -> "RxBuddyAnswer":
    """Return a standardised UNKNOWN_DRUG response when no source can be found."""
    return RxBuddyAnswer(
        verdict="CONSULT_PHARMACIST",
        intent=QuestionIntent.GENERAL.value,
        retrieval_status=RetrievalStatus.LABEL_NOT_FOUND.value,
        confidence="LOW",
        short_answer=_UNKNOWN_DRUG_MESSAGE,
        rationale=[],
        what_to_do=[
            "Verify the spelling of the medication name.",
            "Consult a licensed pharmacist or physician.",
            f"Search DailyMed directly: https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={drug_name}",
        ],
        emergency_escalation=[
            "If you are experiencing an adverse reaction, call 911 or Poison Control immediately.",
        ],
        citations=[],
        last_reviewed=fetched_at,
    )


def build_refused_answer(intent: QuestionIntent, fetched_at: str) -> RxBuddyAnswer:
    """
    Build a standardised INSUFFICIENT_DATA response when the retrieval guard fires.
    Always directs the user to the official label source and a healthcare provider.
    """
    message = _REFUSED_MESSAGES.get(
        intent,
        "Official drug label data could not be retrieved for this question. "
        "Please consult a licensed healthcare provider."
    )
    return RxBuddyAnswer(
        verdict="INSUFFICIENT_DATA",
        intent=intent.value,
        retrieval_status=RetrievalStatus.REFUSED_NO_SOURCE.value,
        confidence="LOW",
        short_answer=message,
        rationale=[],
        what_to_do=[
            "Consult a licensed pharmacist or physician.",
            "Check the official drug label at dailymed.nlm.nih.gov.",
        ],
        emergency_escalation=[
            "If you experience an unexpected reaction, call 911 immediately.",
        ],
        citations=[
            Citation(
                id="cit_ref_0",
                source="DailyMed",
                source_url="https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm",
                section="general",
                section_label="Official Drug Label Database",
                drug_name="",
                date_fetched=fetched_at,
            )
        ],
        last_reviewed=fetched_at,
    )


# ── 6. SAMPLE ANSWER OUTPUT (for documentation) ───────────────────────────────
# The following shows what RxBuddyAnswer looks like for 5 representative queries.
# These are illustrative only — actual answers come from retrieved label data + Claude.

SAMPLE_OUTPUTS: dict[str, dict] = {
    "ibuprofen + warfarin (interaction)": {
        "verdict": "AVOID",
        "intent": "interaction",
        "retrieval_status": "LABEL_FOUND",
        "confidence": "HIGH",
        "short_answer": "Do not take ibuprofen and warfarin together — major bleeding risk.",
        "rationale": [
            {"point": "NSAIDs like ibuprofen inhibit platelet aggregation and can displace warfarin "
                       "from protein binding sites, significantly increasing anticoagulant effect "
                       "and risk of serious or fatal bleeding.",
             "citation_ref": "cit_0"},
        ],
        "what_to_do": [
            "Use acetaminophen (Tylenol) for pain relief instead of ibuprofen.",
            "Tell your prescriber about all OTC medications you take.",
        ],
        "emergency_escalation": [
            "Unusual or prolonged bleeding (cuts, gums, nose)",
            "Blood in urine or stool",
            "Severe headache or dizziness — call 911 immediately",
        ],
        "citations": [{
            "id": "cit_0",
            "source": "DailyMed",
            "source_url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>",
            "section": "drug_interactions",
            "section_label": "Drug Interactions",
            "drug_name": "warfarin",
            "set_id": "<openfda-set-id>",
            "nda_number": "NDA009218",
            "label_revision_date": "2023-06-01",
            "date_fetched": "2026-03-19T10:00:00Z",
        }],
        "last_reviewed": "2026-03-19T10:00:00Z",
    },
    "ibuprofen dosage (refused — dosing without label)": {
        "verdict": "INSUFFICIENT_DATA",
        "intent": "dosing",
        "retrieval_status": "REFUSED_NO_SOURCE",
        "confidence": "LOW",
        "short_answer": "Dosing information requires the official drug label, which could not be retrieved.",
        "rationale": [],
        "what_to_do": [
            "Consult a licensed pharmacist or physician.",
            "Check the official drug label at dailymed.nlm.nih.gov.",
        ],
        "emergency_escalation": ["If you experience an unexpected reaction, call 911 immediately."],
        "citations": [{
            "id": "cit_ref_0",
            "source": "DailyMed",
            "source_url": "https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm",
            "section": "general",
            "section_label": "Official Drug Label Database",
            "drug_name": "",
            "date_fetched": "2026-03-19T10:00:00Z",
        }],
        "last_reviewed": "2026-03-19T10:00:00Z",
    },
    "sertraline during pregnancy (pregnancy_lactation)": {
        "verdict": "CONSULT_CLINICIAN",
        "intent": "pregnancy_lactation",
        "retrieval_status": "LABEL_FOUND",
        "confidence": "HIGH",
        "short_answer": "Sertraline use in pregnancy requires a risk-benefit discussion with your doctor.",
        "rationale": [
            {"point": "FDA prescribing information notes potential neonatal adaptation syndrome "
                       "with third-trimester SSRI use. Risk of untreated depression must be weighed "
                       "against potential fetal risk.",
             "citation_ref": "cit_0"},
        ],
        "what_to_do": [
            "Do not stop sertraline abruptly without talking to your prescriber.",
            "Discuss the risk-benefit balance with your OB/GYN and psychiatrist together.",
        ],
        "emergency_escalation": [
            "Thoughts of self-harm — call 988 (Suicide & Crisis Lifeline) or 911",
        ],
        "citations": [{
            "id": "cit_0",
            "source": "DailyMed",
            "source_url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>",
            "section": "pregnancy",
            "section_label": "Pregnancy",
            "drug_name": "sertraline",
            "label_revision_date": "2024-01-15",
            "date_fetched": "2026-03-19T10:00:00Z",
        }],
        "last_reviewed": "2026-03-19T10:00:00Z",
    },
    "metformin + ibuprofen (interaction + CAUTION)": {
        "verdict": "CAUTION",
        "intent": "interaction",
        "retrieval_status": "LABEL_FOUND",
        "confidence": "HIGH",
        "short_answer": "Use with caution — ibuprofen may impair kidney function and raise metformin levels.",
        "rationale": [
            {"point": "NSAIDs can reduce renal prostaglandin synthesis, decreasing glomerular "
                       "filtration rate. Since metformin is renally cleared, impaired kidneys "
                       "raise metformin plasma levels and lactic acidosis risk.",
             "citation_ref": "cit_0"},
        ],
        "what_to_do": [
            "Use the lowest effective ibuprofen dose for the shortest time.",
            "Consider acetaminophen as an alternative if renal function is borderline.",
            "Monitor for signs of lactic acidosis: muscle pain, weakness, nausea.",
        ],
        "emergency_escalation": [
            "Severe muscle pain or weakness",
            "Difficulty breathing or unusual fatigue — call 911",
        ],
        "citations": [{
            "id": "cit_0",
            "source": "DailyMed",
            "source_url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>",
            "section": "drug_interactions",
            "section_label": "Drug Interactions",
            "drug_name": "metformin",
            "nda_number": "NDA021202",
            "label_revision_date": "2023-04-15",
            "date_fetched": "2026-03-19T10:00:00Z",
        }],
        "last_reviewed": "2026-03-19T10:00:00Z",
    },
    "alcohol + amoxicillin (food_alcohol)": {
        "verdict": "CAUTION",
        "intent": "food_alcohol",
        "retrieval_status": "LABEL_FOUND",
        "confidence": "MEDIUM",
        "short_answer": "Moderate alcohol use is unlikely to directly interact with amoxicillin, "
                         "but alcohol may impair your immune response and recovery.",
        "rationale": [
            {"point": "Amoxicillin labeling does not list alcohol as a contraindicated combination, "
                       "but alcohol can impair immune function, dehydrate the body, and worsen "
                       "antibiotic side effects such as GI upset.",
             "citation_ref": "cit_0"},
        ],
        "what_to_do": [
            "Avoid alcohol while completing your antibiotic course.",
            "Stay well hydrated.",
        ],
        "emergency_escalation": [
            "Severe allergic reaction (hives, throat swelling, difficulty breathing) — call 911",
        ],
        "citations": [{
            "id": "cit_0",
            "source": "DailyMed",
            "source_url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>",
            "section": "drug_interactions",
            "section_label": "Drug Interactions",
            "drug_name": "amoxicillin",
            "label_revision_date": "2022-11-10",
            "date_fetched": "2026-03-19T10:00:00Z",
        }],
        "last_reviewed": "2026-03-19T10:00:00Z",
    },
}


# ── 7. ISOLATED PER-INTENT PROMPT BUILDERS ────────────────────────────────────
#
# Each function returns (system_prompt, user_content).
# ZERO shared fallback paths between intents — no cross-calling.

_OUTPUT_FORMAT = """
REQUIRED OUTPUT FORMAT — all labels UPPERCASE, no markdown:
VERDICT: [AVOID / CAUTION / CONSULT_PHARMACIST / SAFE]
ANSWER: [one decisive sentence]
WARNING: [one sentence — omit this line entirely if not applicable]
DETAILS: [fact 1] | [fact 2] | [fact 3]
ACTION: [action 1] | [action 2] | [action 3]
ARTICLE: [1-3 sentence explanation of mechanism or clinical context]
CONFIDENCE: [HIGH / MEDIUM / LOW]
SOURCES: [source 1 | source 2]

FORMAT RULES:
- All labels UPPERCASE exactly as shown
- No markdown, no bullet prefixes, no JSON
- 2-4 pipe-separated items in DETAILS and ACTION
- WARNING line omitted (not blank) when not applicable"""


def _prompt_side_effects(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist answering a side effects question.

SCOPE: Answer ONLY about the side effects of {drug}.

━━━ HARD RULES — THESE CANNOT BE BROKEN ━━━
1. Do NOT mention any other drug by name
2. Do NOT discuss drug-drug interactions of any kind
3. Do NOT mention MAOIs, serotonin syndrome, phenelzine, linezolid, or any interaction drug
4. Do NOT use verdict AVOID — side effects questions are never AVOID
5. VERDICT must be CAUTION — every drug has side effects that warrant monitoring
6. The ANSWER line describes what side effects {drug} itself causes in the body
7. Do NOT say "do not take with MAOIs" or any interaction warning in any section
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA (use only the adverse reactions and warnings sections):\n"
            f"{fda_context}\n\n"
            f"List the side effects of {drug}. "
            f"Do NOT mention other drugs or interactions."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"List the common and serious side effects of {drug}. "
            f"Do NOT mention other drugs or interactions."
        )
    return system_prompt, user_content


def _prompt_what_is(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist explaining what a medication is and does.

SCOPE: Answer ONLY about {drug}.

━━━ HARD RULES ━━━
1. Explain: drug class, medical condition(s) treated, mechanism of action
2. Do NOT discuss drug interactions unless the patient explicitly asked
3. VERDICT must be SAFE or CONSULT_PHARMACIST — never AVOID for an informational question
4. Do NOT mention other specific drugs by name unless explaining drug class comparisons
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Explain what {drug} is, what conditions it treats, and how it works."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"Explain what {drug} is, what conditions it treats, and how it works."
        )
    return system_prompt, user_content


def _prompt_interaction(
    question: str,
    drug1: str,
    drug2: str,
    all_drugs: "list[str]",
    fda_context: str,
) -> "tuple[str, str]":
    drugs_str = " and ".join(all_drugs) if all_drugs else f"{drug1} and {drug2}"
    system_prompt = f"""You are a clinical pharmacist evaluating a drug interaction.

SCOPE: Answer ONLY about the interaction between {drugs_str}.

━━━ HARD RULES ━━━
1. State specifically whether {drugs_str} interact and how
2. Explain: mechanism, severity, and what the patient should do
3. VERDICT: SAFE if no clinically significant interaction, CAUTION if moderate risk,
   AVOID if contraindicated or major interaction
4. Do NOT discuss drugs other than {drugs_str}
5. Be decisive — state the risk level clearly without hedging
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUGS: {drugs_str}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Evaluate the interaction between {drugs_str}."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUGS: {drugs_str}\n\n"
            f"Evaluate the interaction between {drugs_str}."
        )
    return system_prompt, user_content


def _prompt_dosing(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist answering a dosing question.

SCOPE: Answer ONLY about dosing of {drug}.

━━━ HARD RULES ━━━
1. Provide: starting dose, typical dose, max daily dose, frequency
2. Include renal or hepatic adjustments if the label mentions them
3. VERDICT must be CONSULT_PHARMACIST — dosing requires individual confirmation
4. Do NOT discuss drug interactions or side effects
5. Do NOT discuss other drugs
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Provide standard dosing information for {drug}."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"Provide standard dosing information for {drug}."
        )
    return system_prompt, user_content


def _prompt_safety(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist evaluating medication safety in a specific context.

SCOPE: Answer ONLY about {drug} in the context described in the question.

━━━ HARD RULES ━━━
1. Answer directly: is {drug} safe in this specific situation?
2. VERDICT: SAFE if generally safe, CAUTION if conditional, AVOID if contraindicated
3. Do not be vague — state the risk level with a specific reason
4. Do NOT discuss interactions with other drugs unless that is what was asked
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Is {drug} safe in the context described above?"
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"Is {drug} safe in the context described above?"
        )
    return system_prompt, user_content


def _prompt_contraindications(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist explaining contraindications.

SCOPE: Answer ONLY about who should NOT take {drug}.

━━━ HARD RULES ━━━
1. List specific conditions, patient populations, and drug classes that are contraindicated
2. VERDICT: AVOID if clearly contraindicated for the patient's situation,
   CONSULT_PHARMACIST if it depends on individual factors
3. Do NOT discuss dosing in detail
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"What are the contraindications for {drug}?"
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"What are the contraindications for {drug}?"
        )
    return system_prompt, user_content


def _prompt_pregnancy(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist evaluating medication safety during pregnancy or breastfeeding.

SCOPE: Answer ONLY about {drug} in pregnancy or lactation.

━━━ HARD RULES ━━━
1. State the FDA pregnancy risk evidence or equivalent
2. VERDICT: AVOID only if clearly teratogenic or contraindicated;
   CONSULT_PHARMACIST if risk-benefit decision is needed;
   CAUTION if limited or conflicting data
3. Always recommend consulting OB/GYN or pharmacist for final decision
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Is {drug} safe during pregnancy or breastfeeding?"
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"Is {drug} safe during pregnancy or breastfeeding?"
        )
    return system_prompt, user_content


def _prompt_food_alcohol(question: str, drug: str, fda_context: str) -> "tuple[str, str]":
    system_prompt = f"""You are a clinical pharmacist answering a food or alcohol interaction question.

SCOPE: Answer ONLY about {drug} and the specific food or drink in the question.

━━━ HARD RULES ━━━
1. Directly answer whether the food/alcohol combination is safe with {drug}
2. State the mechanism if a real interaction exists
3. VERDICT: SAFE, CAUTION, or AVOID based on the evidence
4. Do NOT discuss unrelated drug-drug interactions
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Evaluate {drug} with the food or drink mentioned in the question."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n"
            f"DRUG: {drug}\n\n"
            f"Evaluate {drug} with the food or drink mentioned in the question."
        )
    return system_prompt, user_content


def _prompt_general(
    question: str, drug_names: "list[str]", fda_context: str
) -> "tuple[str, str]":
    drug_str = ", ".join(drug_names) if drug_names else "the medication"
    system_prompt = f"""You are a clinical pharmacist answering a medication question.

DRUGS: {drug_str}

━━━ HARD RULES ━━━
1. Answer the specific question asked about {drug_str}
2. Be decisive — state the risk level with a specific reason
3. Answer ONLY about {drug_str}
{_OUTPUT_FORMAT}"""

    if fda_context:
        user_content = (
            f"PATIENT QUESTION: {question}\n\n"
            f"FDA LABEL DATA:\n{fda_context}\n\n"
            f"Answer the question about {drug_str}."
        )
    else:
        user_content = (
            f"PATIENT QUESTION: {question}\n\n"
            f"Answer the question about {drug_str}."
        )
    return system_prompt, user_content


def build_intent_prompt(
    intent_str: str,
    question: str,
    drug_names: "list[str]",
    drug_name: str,
    fda_context: str,
) -> "tuple[str, str]":
    """
    Return (system_prompt, user_content) for the given intent.

    Each intent has a COMPLETELY ISOLATED prompt — no shared fallback paths,
    no cross-calling between engines.

    SIDE_EFFECTS will never produce MAOI interaction warnings.
    WHAT_IS will never produce AVOID verdicts.
    DOSING will always produce CONSULT_PHARMACIST verdicts.
    """
    primary = drug_name or (drug_names[0] if drug_names else "the medication")

    try:
        intent = QuestionIntent(intent_str)
    except ValueError:
        intent = QuestionIntent.GENERAL

    if intent == QuestionIntent.SIDE_EFFECTS:
        return _prompt_side_effects(question, primary, fda_context)

    if intent == QuestionIntent.WHAT_IS:
        return _prompt_what_is(question, primary, fda_context)

    if intent == QuestionIntent.INTERACTION:
        d1 = drug_names[0] if drug_names else primary
        d2 = drug_names[1] if len(drug_names) > 1 else "the second drug"
        return _prompt_interaction(question, d1, d2, drug_names, fda_context)

    if intent == QuestionIntent.DOSING:
        return _prompt_dosing(question, primary, fda_context)

    if intent == QuestionIntent.SAFETY:
        return _prompt_safety(question, primary, fda_context)

    if intent == QuestionIntent.CONTRAINDICATIONS:
        return _prompt_contraindications(question, primary, fda_context)

    if intent == QuestionIntent.PREGNANCY_LACTATION:
        return _prompt_pregnancy(question, primary, fda_context)

    if intent == QuestionIntent.FOOD_ALCOHOL:
        return _prompt_food_alcohol(question, primary, fda_context)

    return _prompt_general(question, drug_names, fda_context)


# ── 8. VERDICT GUARDRAILS ─────────────────────────────────────────────────────

def enforce_verdict_by_intent(intent_str: str, answer_text: str) -> str:
    """
    Post-process Claude's answer to enforce intent-specific verdict rules.

    Rules:
      SIDE_EFFECTS   → verdict must be CAUTION (never AVOID or SAFE)
      WHAT_IS        → verdict must be SAFE or CONSULT_PHARMACIST (never AVOID)
      DOSING         → verdict must be CONSULT_PHARMACIST always

    Applied AFTER Claude + Haiku validation so the final answer is always correct.
    """
    try:
        intent = QuestionIntent(intent_str)
    except ValueError:
        return answer_text

    lines = answer_text.split("\n")
    result: list[str] = []

    for line in lines:
        stripped_upper = line.strip().upper()
        if stripped_upper.startswith("VERDICT:") and ":" in line:
            current = line.split(":", 1)[1].strip().upper()

            if intent == QuestionIntent.SIDE_EFFECTS:
                if current not in ("CAUTION",):
                    _logger.info(
                        "[VerdictGuard] SIDE_EFFECTS: overriding %s → CAUTION", current
                    )
                    line = "VERDICT: CAUTION"

            elif intent == QuestionIntent.WHAT_IS:
                if current == "AVOID":
                    _logger.info("[VerdictGuard] WHAT_IS: overriding AVOID → CONSULT_PHARMACIST")
                    line = "VERDICT: CONSULT_PHARMACIST"

            elif intent == QuestionIntent.DOSING:
                if current != "CONSULT_PHARMACIST":
                    _logger.info(
                        "[VerdictGuard] DOSING: overriding %s → CONSULT_PHARMACIST", current
                    )
                    line = "VERDICT: CONSULT_PHARMACIST"

        result.append(line)

    return "\n".join(result)


# ── 9. DRUG SCOPE GUARD ───────────────────────────────────────────────────────

# Drug names / interaction drug-classes that must NEVER appear in a single-drug
# side-effects answer if they were not part of the original query.
_SCOPE_GUARD_KEYWORDS: frozenset[str] = frozenset({
    "maoi", "monoamine oxidase", "phenelzine", "tranylcypromine",
    "isocarboxazid", "selegiline", "rasagiline", "linezolid",
    "methylene blue", "serotonin syndrome",
})

# Filterable section prefixes — safe to remove content from these
_FILTERABLE_PREFIXES: tuple[str, ...] = ("DETAILS:", "ACTION:", "WARNING:", "ARTICLE:")


def strip_off_topic_drugs(
    query_drugs: "list[str]",
    answer_text: str,
    intent_str: str,
) -> str:
    """
    For single-drug intents (SIDE_EFFECTS, WHAT_IS, DOSING, CONTRAINDICATIONS):
    remove lines/items from DETAILS, ACTION, WARNING, and ARTICLE that mention
    drug names or interaction classes not present in the original query.

    This is the last-resort guard that prevents MAOI interaction warnings from
    appearing in a duloxetine side effects answer even if the prompt guardrail
    was insufficient.

    ANSWER, VERDICT, CONFIDENCE, and SOURCES are never modified.
    """
    try:
        intent = QuestionIntent(intent_str)
    except ValueError:
        return answer_text

    # Only apply to single-drug focused intents
    if intent not in (
        QuestionIntent.SIDE_EFFECTS,
        QuestionIntent.WHAT_IS,
        QuestionIntent.DOSING,
        QuestionIntent.CONTRAINDICATIONS,
        QuestionIntent.PREGNANCY_LACTATION,
    ):
        return answer_text

    query_lower: set[str] = {d.lower() for d in (query_drugs or [])}

    # Build watch-set: scope guard keywords NOT already in query
    watch_set: set[str] = {kw for kw in _SCOPE_GUARD_KEYWORDS if kw not in query_lower}

    # Also watch HIGH_RISK_DRUGS that aren't in the query (≥5 chars to avoid false positives)
    watch_set.update(
        d for d in HIGH_RISK_DRUGS
        if d not in query_lower and len(d) >= 5
    )

    if not watch_set:
        return answer_text

    lines = answer_text.split("\n")
    result_lines: list[str] = []

    for line in lines:
        line_lower = line.lower()
        upper = line.strip().upper()

        is_filterable = any(upper.startswith(pfx) for pfx in _FILTERABLE_PREFIXES)

        if not is_filterable:
            result_lines.append(line)
            continue

        is_detail_or_action = upper.startswith("DETAILS:") or upper.startswith("ACTION:")

        if is_detail_or_action and ":" in line:
            # Filter individual pipe-separated items
            prefix, items_str = line.split(":", 1)
            items = [i.strip() for i in items_str.split("|")]
            clean_items: list[str] = []
            for item in items:
                if any(kw in item.lower() for kw in watch_set):
                    _logger.info(
                        "[ScopeGuard] Stripped %s item mentioning off-topic drug: %.60s",
                        prefix.strip(), item,
                    )
                else:
                    clean_items.append(item)
            if clean_items:
                result_lines.append(f"{prefix}: {' | '.join(clean_items)}")
            # else: all items stripped → omit the line entirely
        else:
            # WARNING or ARTICLE: remove the whole line if it mentions off-topic drugs
            if any(kw in line_lower for kw in watch_set):
                _logger.info(
                    "[ScopeGuard] Stripped %s section mentioning off-topic drug",
                    upper.split(":")[0].strip(),
                )
            else:
                result_lines.append(line)

    return "\n".join(result_lines)
