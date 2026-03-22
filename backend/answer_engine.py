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

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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


def classify_intent(question: str, drug_count: int = 0) -> QuestionIntent:
    """
    Classify a user question into one of 9 intents.

    Priority order (first match wins):
      INTERACTION (2+ drugs OR strong kw) > WHAT_IS > SIDE_EFFECTS > DOSING >
      SAFETY > CONTRAINDICATIONS > PREGNANCY_LACTATION > FOOD_ALCOHOL > GENERAL

    Examples
    --------
    "can I take ibuprofen with warfarin?"         → INTERACTION
    "what is metformin used for?"                 → WHAT_IS
    "lisinopril is for what"                      → WHAT_IS
    "what are the side effects of metformin?"     → SIDE_EFFECTS
    "how much ibuprofen can I take?"              → DOSING
    "is sertraline safe during pregnancy?"        → SAFETY
    "can I take ibuprofen if I'm allergic?"       → CONTRAINDICATIONS
    "can I drink alcohol with amoxicillin?"       → FOOD_ALCOHOL
    "risperidone and heart problems"              → INTERACTION (2 drugs-ish)
    """
    q = question.lower()

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
