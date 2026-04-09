"""
Deterministic verdict evaluation, pairwise interaction logic, and answer parsing.

All verdict decisions are made here — Claude NEVER decides the verdict.
Legacy functions (_legacy_extract_verdict_pre_regex, _legacy_extract_verdict_pre_pairwise,
_legacy_validate_and_correct_verdict) have been removed.
"""

from __future__ import annotations

import re
from typing import Callable


# ── Deterministic verdict table ──────────────────────────────────────────────
# Pre-resolved answers for the highest-stakes drug combinations.
# These SHORT-CIRCUIT Claude — no API call, no hallucination risk.

DETERMINISTIC_VERDICTS: dict[frozenset, tuple[str, str]] = {
    # Anticoagulant + NSAID → major bleeding
    frozenset({"warfarin",    "aspirin"}):    ("AVOID",    "Do not combine warfarin and aspirin without medical supervision — the risk of serious bleeding is significantly increased."),
    frozenset({"warfarin",    "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with warfarin — NSAIDs substantially raise your bleeding risk and can raise warfarin levels."),
    frozenset({"warfarin",    "naproxen"}):   ("AVOID",    "Do not take naproxen with warfarin — this combination significantly increases the risk of serious or fatal bleeding."),
    frozenset({"warfarin",    "diclofenac"}): ("AVOID",    "Do not take diclofenac with warfarin — the combination raises bleeding risk and can make anticoagulation unstable."),
    frozenset({"apixaban",    "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with apixaban (Eliquis) — NSAIDs increase bleeding risk with all blood thinners."),
    frozenset({"apixaban",    "aspirin"}):    ("CAUTION",  "Use extreme caution combining apixaban and aspirin — dual use significantly raises bleeding risk; only do so if a doctor has explicitly prescribed both."),
    frozenset({"rivaroxaban", "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with rivaroxaban (Xarelto) — NSAIDs increase bleeding risk when combined with blood thinners."),
    frozenset({"rivaroxaban", "aspirin"}):    ("CAUTION",  "Use extreme caution — combining rivaroxaban with aspirin increases bleeding risk and should only be done under medical supervision."),
    frozenset({"dabigatran",  "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with dabigatran (Pradaxa) — this combination significantly increases the risk of gastrointestinal bleeding."),
    # Nitrate + PDE-5 → severe hypotension / fatal
    frozenset({"sildenafil",  "nitroglycerin"}):           ("AVOID", "Never combine sildenafil (Viagra) with nitroglycerin — the combination can cause a life-threatening drop in blood pressure."),
    frozenset({"tadalafil",   "nitroglycerin"}):           ("AVOID", "Never combine tadalafil (Cialis) with nitroglycerin — this can cause a dangerous and potentially fatal drop in blood pressure."),
    frozenset({"sildenafil",  "isosorbide mononitrate"}):  ("AVOID", "Never combine sildenafil with isosorbide mononitrate — the combination causes severe, potentially fatal hypotension."),
    # Serotonin syndrome
    frozenset({"sertraline",  "tramadol"}):   ("AVOID",    "Do not take tramadol with sertraline — this combination can cause serotonin syndrome, a potentially life-threatening condition."),
    frozenset({"fluoxetine",  "tramadol"}):   ("AVOID",    "Do not take tramadol with fluoxetine — the combination raises the risk of serotonin syndrome, which can be life-threatening."),
    frozenset({"linezolid",   "sertraline"}): ("AVOID",    "Do not take linezolid with sertraline — linezolid has MAOI properties that can cause fatal serotonin syndrome."),
    # Narrow-TI drug toxicity
    frozenset({"lithium",     "ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with lithium — NSAIDs reduce lithium excretion and can quickly push levels into the toxic range."),
    frozenset({"lithium",     "naproxen"}):   ("AVOID",    "Do not take naproxen with lithium — NSAIDs impair lithium clearance and can cause toxicity."),
    frozenset({"digoxin",     "amiodarone"}): ("AVOID",    "Do not combine digoxin with amiodarone without close monitoring — amiodarone doubles digoxin levels, risking toxicity."),
    frozenset({"digoxin",     "verapamil"}):  ("AVOID",    "Do not combine digoxin with verapamil without medical supervision — verapamil raises digoxin levels and increases toxicity risk."),
    frozenset({"methotrexate","ibuprofen"}):  ("AVOID",    "Do not take ibuprofen with methotrexate — NSAIDs reduce methotrexate clearance and can cause serious or fatal toxicity."),
    frozenset({"methotrexate","naproxen"}):   ("AVOID",    "Do not take naproxen with methotrexate — this combination can cause life-threatening methotrexate toxicity."),
    # Retinoid + tetracycline → pseudotumor cerebri
    frozenset({"isotretinoin","doxycycline"}):("AVOID",    "Do not combine isotretinoin with doxycycline — both drugs raise intracranial pressure and the combination risks pseudotumor cerebri."),
    frozenset({"isotretinoin","minocycline"}):("AVOID",    "Do not combine isotretinoin with minocycline — this combination significantly increases the risk of dangerously raised intracranial pressure."),
    # Kidney / lactic acidosis risk
    frozenset({"metformin",   "ibuprofen"}):  ("CAUTION",  "Use caution — ibuprofen can impair kidney function, which reduces metformin clearance and raises lactic acidosis risk; prefer acetaminophen for pain."),
    frozenset({"lisinopril",  "ibuprofen"}):  ("CAUTION",  "Use caution — ibuprofen reduces the blood-pressure-lowering effect of lisinopril and together with an ACE inhibitor can cause acute kidney injury."),
    frozenset({"lisinopril",  "potassium chloride"}): ("CAUTION", "Use caution — lisinopril already raises potassium levels; adding potassium supplements can cause dangerously high potassium (hyperkalemia)."),
}

_ACETAMINOPHEN_ALCOHOL_DIRECT = (
    "CAUTION",
    "Use caution — occasional light drinking is unlikely to cause harm, but regular or heavy alcohol use with acetaminophen significantly increases the risk of liver damage.",
)

# ── Phrase lists ─────────────────────────────────────────────────────────────

DOSAGE_TERMS = (
    "dosage", "dose", "how much", "how many", "how to take",
    "when to take", "maximum dose", "max dose", "mg", "milligram",
    "dosing", "strength", "how often",
)

SIDE_EFFECT_TERMS = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "reaction", "reactions", "symptom", "symptoms",
)

INFORMATIONAL_TERMS = (
    "side effect", "side effects", "adverse effect", "adverse effects",
    "what is", "what are", "how does", "explain", "reaction", "reactions",
)

AVOID_PHRASES = (
    "avoid taking", "do not take", "not recommended", "bleeding risk",
    "contraindicated", "should not be taken together", "dangerous combination",
    "increased risk of bleeding", "major interaction", "severe interaction",
    "serious interaction", "avoid this combination", "do not combine",
    "not safe together", "should be avoided", "high risk", "black box warning",
)

CAUTION_PHRASES = (
    "moderate interaction", "use with caution", "monitor", "monitoring",
    "may increase risk", "can increase risk", "increased risk", "kidney strain",
    "kidney stress", "renal risk", "renal impairment", "lactic acidosis risk",
    "may worsen", "can worsen", "not ideal", "be careful", "watch for side effects",
    "needs closer monitoring", "dose adjustment may be needed",
    "generally well tolerated but", "slight risk", "small risk", "rare risk",
    "may slightly increase", "not completely safe", "should be monitored",
    "mild interaction", "possible interaction",
)

SAFE_PHRASES = (
    "no known interaction", "no significant interaction", "generally safe",
    "no clinically significant interaction", "safe to take together",
    "no major interaction", "compatible together", "typically safe",
    "low interaction risk",
)

NSAID_DRUGS = {"ibuprofen", "naproxen", "aspirin"}
ANTICOAGULANT_DRUGS = {"warfarin", "heparin"}
RENAL_RISK_DRUGS = {"metformin", "lisinopril", "losartan"}

EXPLICIT_PAIR_RISKS: dict[tuple[str, str], str] = {
    ("ibuprofen", "warfarin"): "AVOID",
    ("naproxen", "warfarin"): "AVOID",
    ("aspirin", "warfarin"): "AVOID",
    ("heparin", "ibuprofen"): "AVOID",
    ("heparin", "naproxen"): "AVOID",
    ("heparin", "aspirin"): "AVOID",
    ("ibuprofen", "metformin"): "CAUTION",
    ("ibuprofen", "lisinopril"): "CAUTION",
    ("ibuprofen", "losartan"): "CAUTION",
    ("naproxen", "metformin"): "CAUTION",
    ("naproxen", "lisinopril"): "CAUTION",
    ("naproxen", "losartan"): "CAUTION",
    ("aspirin", "metformin"): "CAUTION",
    ("aspirin", "lisinopril"): "CAUTION",
    ("aspirin", "losartan"): "CAUTION",
}

_VAGUE_BANNED: frozenset[str] = frozenset({
    "it depends", "generally okay", "generally safe", "may be safe",
    "could be safe", "might be safe", "usually safe", "typically safe",
    "often safe", "in most cases", "for most people", "varies",
    "you should consult", "check with your", "always check", "be careful",
})

_CORRUPTED_PREFIXES = (
    "** classification", "** answer**", "**:**", "**primary intent", "** ** **", "---",
)
_CORRUPTED_EXACT: frozenset[str] = frozenset({
    "needs review.", "**.", "** **", "n/a", "none",
    "needs review", "n/a.", "none.", "undefined", "null", ".",
})
_CORRUPTED_SUBSTRINGS = (
    "category 6", "category 5", "category 4", "category 3",
    "primary intent category", "needs review", "answer: why:",
    "intent classification", " --- ", "\n---", "---\n", "undefined",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _contains_any(haystack: str, phrases: tuple[str, ...] | list[str]) -> bool:
    return any(phrase in haystack for phrase in phrases)


def _normalize_pair(drug_a: str, drug_b: str) -> tuple[str, str]:
    return tuple(sorted((drug_a.strip().lower(), drug_b.strip().lower())))


# ── Pairwise interaction evaluation ─────────────────────────────────────────

def evaluate_pair_interaction(drug_a: str, drug_b: str) -> str:
    """Deterministic backend classification for a drug pair."""
    left, right = _normalize_pair(drug_a, drug_b)
    if left == right:
        return "SAFE"
    explicit = EXPLICIT_PAIR_RISKS.get((left, right))
    if explicit:
        return explicit
    pair_set = {left, right}
    if pair_set & NSAID_DRUGS and pair_set & ANTICOAGULANT_DRUGS:
        return "AVOID"
    if pair_set & NSAID_DRUGS and pair_set & RENAL_RISK_DRUGS:
        return "CAUTION"
    return "SAFE"


def evaluate_pairwise_interactions(drugs: list[str]) -> tuple[str, dict[str, list[str]]]:
    """Evaluate all unique drug pairs. Returns (aggregate_verdict, summary)."""
    unique_drugs = list(dict.fromkeys(d.strip().lower() for d in drugs if d and d.strip()))
    summary: dict[str, list[str]] = {"avoid_pairs": [], "caution_pairs": []}

    if len(unique_drugs) < 2:
        return "CONSULT_PHARMACIST", summary

    saw_caution = False
    for i, left in enumerate(unique_drugs):
        for right in unique_drugs[i + 1 :]:
            pair_verdict = evaluate_pair_interaction(left, right)
            label = f"{_normalize_pair(left, right)[0]} + {_normalize_pair(left, right)[1]}"
            if pair_verdict == "AVOID":
                summary["avoid_pairs"].append(label)
            elif pair_verdict == "CAUTION":
                summary["caution_pairs"].append(label)
                saw_caution = True

    if summary["avoid_pairs"]:
        return "AVOID", summary
    if saw_caution:
        return "CAUTION", summary
    return "SAFE", summary


def build_interaction_summary(
    question: str,
    extract_drug_names_fn: Callable[[str], list[str]],
    all_known_drugs: list[str] | None = None,
) -> dict[str, list[str]]:
    """Build interaction summary for a question."""
    drugs = extract_drug_names_fn(question)
    if len(drugs) < 2 and all_known_drugs:
        q_lower = question.lower()
        for known in all_known_drugs:
            if known in q_lower and known not in drugs:
                drugs.append(known)
    _, summary = evaluate_pairwise_interactions(list(dict.fromkeys(drugs)))
    return summary


# ── Deterministic lookup ─────────────────────────────────────────────────────

def lookup_deterministic(drug_names: list[str], intent: str) -> tuple[str, str] | None:
    """Check DETERMINISTIC_VERDICTS for a pre-resolved answer."""
    lower = {d.lower() for d in drug_names}
    pair = frozenset(lower)

    alcohol_terms = {"alcohol", "drinking", "drink", "beer", "wine"}
    aceta_terms = {"acetaminophen", "tylenol", "paracetamol"}
    if intent in ("food_alcohol", "side_effects", "interaction") and lower & alcohol_terms and lower & aceta_terms:
        return _ACETAMINOPHEN_ALCOHOL_DIRECT

    if len(lower) >= 2:
        return DETERMINISTIC_VERDICTS.get(pair)
    return None


# ── Vague-phrase check ───────────────────────────────────────────────────────

def check_vague(answer_text: str) -> bool:
    """Return True if the ANSWER/DIRECT line contains banned vague phrases."""
    for line in answer_text.splitlines():
        upper = line.strip().upper()
        if upper.startswith("ANSWER:") or upper.startswith("DIRECT:"):
            line_lower = line.lower()
            return any(phrase in line_lower for phrase in _VAGUE_BANNED)
    return False


# ── Corrupted / wrong-drug detection ────────────────────────────────────────

def is_corrupted_db_answer(text: str) -> bool:
    """Return True when a stored DB answer is corrupted or malformed."""
    if text is None:
        return True
    t = text.strip()
    if not t or len(t) < 40:
        return True
    t_lower = t.lower()
    if t_lower in _CORRUPTED_EXACT:
        return True
    if t[0] in (":", ".") or t.startswith("- ") or t.startswith("---"):
        return True
    for prefix in _CORRUPTED_PREFIXES:
        if t_lower.startswith(prefix):
            return True
    for sub in _CORRUPTED_SUBSTRINGS:
        if sub in t_lower:
            return True
    return False


def is_wrong_drug_answer(
    query: str,
    answer_text: str,
    extract_drug_names_fn: Callable[[str], list[str]],
) -> bool:
    """Return True when the answer contains none of the drugs from the query."""
    if not answer_text or not query:
        return False
    drug_names = extract_drug_names_fn(query)
    if not drug_names:
        return False
    answer_lower = answer_text.lower()
    all_names: set[str] = set()
    for drug in drug_names:
        all_names.add(drug.lower())
        try:
            from drug_catalog import find_drug

            rec = find_drug(drug)
            if rec:
                for bn in rec.brand_names:
                    all_names.add(bn.lower())
                for gn in rec.generic_names:
                    all_names.add(gn.lower())
        except Exception:
            pass
    for name in all_names:
        if len(name) >= 4 and name in answer_lower:
            return False
    return True


# ── Verdict extraction (renamed from _extract_verdict) ──────────────────────

def extract_verdict(
    text: str,
    question: str = "",
    query_intent: str = "",
    drug_names: list[str] | None = None,
    all_known_drugs: list[str] | None = None,
) -> str:
    """
    Final deterministic backend verdict extraction.
    Priority order: AVOID > CAUTION > SAFE > CONSULT_PHARMACIST.

    Accepts pre-computed query_intent and drug_names to avoid circular imports.
    """
    if not text:
        return "CONSULT_PHARMACIST"

    normalized_text = text.replace("\r\n", "\n")
    lower_text = normalized_text.lower()
    q_lower = question.lower() if question else ""

    def _extract_explicit_verdict(raw_text: str) -> str | None:
        for raw_line in raw_text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[#*\-\s]+", "", line)
            upper_line = line.upper()
            if upper_line.startswith("ANSWER:") or upper_line.startswith("VERDICT:"):
                val = upper_line.split(":", 1)[1].strip() if ":" in upper_line else ""
                if val.startswith("SAFE") or val.startswith("YES") or val.startswith("USUALLY YES"):
                    return "SAFE"
                if val.startswith("AVOID") or val.startswith("NO"):
                    return "AVOID"
                if val.startswith("CAUTION") or val.startswith("MAYBE") or val.startswith("DEPENDS") or val.startswith("IT DEPENDS"):
                    return "CAUTION"
                if val.startswith("NEEDS REVIEW") or val.startswith("CONSULT") or val.startswith("ASK"):
                    return "CONSULT_PHARMACIST"
        return None

    if query_intent == "dosing" or _contains_any(q_lower, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if query_intent == "side_effects" or _contains_any(q_lower, SIDE_EFFECT_TERMS):
        return "CAUTION"
    if query_intent == "safety_general" and ("water" in q_lower or "food" in q_lower):
        if not _contains_any(lower_text, AVOID_PHRASES) and not _contains_any(lower_text, CAUTION_PHRASES):
            return "SAFE"

    pairwise_verdict = "CONSULT_PHARMACIST"
    if query_intent == "interaction" and drug_names:
        _drugs = list(drug_names)
        if len(_drugs) < 2 and all_known_drugs:
            for known in all_known_drugs:
                if known in q_lower and known not in _drugs:
                    _drugs.append(known)
        _drugs = list(dict.fromkeys(_drugs))
        pairwise_verdict, _ = evaluate_pairwise_interactions(_drugs)
        if pairwise_verdict == "AVOID":
            return "AVOID"
        if pairwise_verdict == "CAUTION":
            return "CAUTION"

    if _contains_any(lower_text, AVOID_PHRASES):
        return "AVOID"
    if _contains_any(lower_text, CAUTION_PHRASES):
        return "CAUTION"

    explicit_verdict = _extract_explicit_verdict(normalized_text)
    if explicit_verdict == "CAUTION" and _contains_any(lower_text, AVOID_PHRASES):
        return "AVOID"
    if explicit_verdict == "SAFE":
        if _contains_any(lower_text, AVOID_PHRASES):
            return "AVOID"
        if _contains_any(lower_text, CAUTION_PHRASES):
            return "CAUTION"
        return "SAFE"
    if explicit_verdict in {"AVOID", "CAUTION", "CONSULT_PHARMACIST"}:
        return explicit_verdict

    upper_text = normalized_text.upper()
    has_safe_signal = _contains_any(lower_text, SAFE_PHRASES) or any(
        phrase in upper_text
        for phrase in (
            "YES, YOU CAN", "YES YOU CAN", "IT IS SAFE", "GENERALLY SAFE",
            "USUALLY SAFE", "TYPICALLY SAFE", "YES,", "ANSWER: YES",
            "SAFETY LEVEL: SAFE",
        )
    )
    if has_safe_signal and not _contains_any(lower_text, CAUTION_PHRASES) and not _contains_any(lower_text, AVOID_PHRASES):
        return "SAFE"

    if any(
        phrase in upper_text
        for phrase in (
            "NO, YOU SHOULD NOT", "NO YOU SHOULD NOT", "DO NOT TAKE",
            "NOT RECOMMENDED", "AVOID TAKING", "SHOULD NOT TAKE",
            "NO,", "ANSWER: NO", "CONTRAINDICATED", "AVOID / CONTRAINDICATED",
        )
    ):
        return "AVOID"

    if any(
        phrase in upper_text
        for phrase in (
            "DEPENDS ON", "IT DEPENDS", "CASE BY CASE", "VARIES",
            "POSSIBLY", "MIGHT BE", "COULD BE", "SOMETIMES", "USE WITH CAUTION",
        )
    ):
        return "CAUTION"

    if query_intent == "interaction" and pairwise_verdict == "SAFE":
        if not _contains_any(lower_text, CAUTION_PHRASES) and not _contains_any(lower_text, AVOID_PHRASES):
            return "SAFE"

    return "CONSULT_PHARMACIST"


# ── Verdict validation ───────────────────────────────────────────────────────

def validate_and_correct_verdict(
    answer_text: str,
    verdict: str,
    question: str = "",
    interaction_summary: dict[str, list[str]] | None = None,
    query_intent: str = "",
) -> str:
    """
    Final backend authority for verdict correction.
    Deterministically reconciles explanation text, intent, and pairwise interaction risk.
    """
    normalized_verdict = (verdict or "").strip().upper() or "CONSULT_PHARMACIST"
    combined_text = f"{question}\n{answer_text}".lower()
    summary = interaction_summary or {"avoid_pairs": [], "caution_pairs": []}

    if query_intent == "dosing" or _contains_any(combined_text, DOSAGE_TERMS):
        return "CONSULT_PHARMACIST"
    if query_intent == "interaction" and summary.get("avoid_pairs"):
        return "AVOID"
    if _contains_any(combined_text, AVOID_PHRASES):
        return "AVOID"
    if query_intent == "interaction" and summary.get("caution_pairs"):
        return "CAUTION"
    if _contains_any(combined_text, CAUTION_PHRASES):
        return "CAUTION"
    if query_intent == "side_effects" or _contains_any(combined_text, INFORMATIONAL_TERMS):
        return "CAUTION"
    if query_intent == "safety_general" and ("water" in combined_text or "food" in combined_text):
        if not _contains_any(combined_text, CAUTION_PHRASES) and not _contains_any(combined_text, AVOID_PHRASES):
            return "SAFE"
    if normalized_verdict == "SAFE" and _contains_any(combined_text, SAFE_PHRASES):
        return "SAFE"
    if normalized_verdict == "SAFE" and _contains_any(combined_text, CAUTION_PHRASES + AVOID_PHRASES):
        return "CAUTION"
    if normalized_verdict == "SAFE" and not _contains_any(combined_text, CAUTION_PHRASES) and not _contains_any(combined_text, AVOID_PHRASES):
        return "SAFE"
    if normalized_verdict in {"AVOID", "CAUTION", "CONSULT_PHARMACIST"}:
        return normalized_verdict
    if _contains_any(combined_text, SAFE_PHRASES):
        return "SAFE"
    return "CONSULT_PHARMACIST"


# ── Answer post-processing ───────────────────────────────────────────────────

def post_process_cached_answer(answer_text: str) -> str:
    """Clean up raw markdown, strip leaked prompt text."""
    if not answer_text:
        return answer_text

    text = answer_text

    leaked_patterns = [
        r"STEP\s*\d+[-:]?\d*\s*[:—-]?",
        r"INTENT\s*CLASSIFICATION",
        r"Primary\s*Intent\s*:",
        r"\*\*Primary\s*Intent\s*:",
        r"NEEDS\s*REVIEW\s*\*\*",
        r"CROSS-EXAMINATION\s*CHECK",
        r"CONTRADICTION\s*BLOCK",
        r"SIMPLICITY\s*RULE",
        r"FINAL\s*SELF-AUDIT",
        r"MISMATCH\s*PREVENTION",
        r"SAFETY\s*FILTER",
        r"DO\s*NOT\s*HALLUCINATE",
        r"VALID\s*INTENT\s*CATEGORIES",
    ]
    for pattern in leaked_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    lines = text.split("\n")
    filtered_lines = []
    for line in lines:
        line_upper = line.strip().upper()
        if any(
            skip in line_upper
            for skip in [
                "STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5",
                "STEP 6", "STEP 7", "STEP 8", "STEP 9", "STEP 10", "STEP 11",
                "INTENT CLASSIFICATION", "PRIMARY INTENT:",
                "READ THE FULL QUESTION", "EXTRACT THE CORE ASK",
                "ANSWER THE EXACT QUESTION", "CROSS-EXAMINATION",
                "CONTRADICTION BLOCK", "SIMPLICITY RULE",
            ]
        ):
            continue
        filtered_lines.append(line)
    text = "\n".join(filtered_lines)

    section_headers = [
        "What to do", "What to avoid", "See a doctor if", "Get medical help",
        "Important notes", "Important note", "Warning", "Warnings",
        "Do", "Avoid", "Doctor", "Answer", "Why", "Verdict",
    ]

    if " - " in text and "\n- " not in text:
        result_lines: list[str] = []
        remaining = text
        while remaining:
            best_match = None
            best_pos = len(remaining)
            for header in section_headers:
                for pat in [f"{header}:", f"{header.lower()}:", f"{header.upper()}:"]:
                    pos = remaining.find(pat)
                    if pos != -1 and pos < best_pos:
                        best_pos = pos
                        best_match = (pos, len(pat), header)
            if best_match:
                pos, pattern_len, header = best_match
                if pos > 0:
                    prefix = remaining[:pos].strip()
                    if prefix:
                        result_lines.append(prefix)
                section_start = pos + pattern_len
                next_header_pos = len(remaining)
                for nh in section_headers:
                    for pat in [f"{nh}:", f"{nh.lower()}:", f"{nh.upper()}:"]:
                        np = remaining.find(pat, section_start)
                        if np != -1 and np < next_header_pos:
                            next_header_pos = np
                section_content = remaining[section_start:next_header_pos].strip()
                result_lines.append(f"\n**{header}:**")
                if " - " in section_content:
                    for item in section_content.split(" - "):
                        item = item.strip()
                        if item:
                            result_lines.append(f"- {item}")
                elif section_content:
                    result_lines.append(section_content)
                remaining = remaining[next_header_pos:]
            else:
                if remaining.strip():
                    result_lines.append(remaining.strip())
                break
        text = "\n".join(result_lines)

    lines = text.split("\n")
    processed_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            processed_lines.append("")
            continue
        if stripped.endswith(":") and len(stripped) < 50 and not stripped.startswith("**"):
            processed_lines.append(f"**{stripped}**")
            continue
        processed_lines.append(stripped)
    return "\n".join(processed_lines)


# ── Structured answer parsing ────────────────────────────────────────────────

def parse_structured_answer(
    answer_text: str,
    question: str = "",
    query_intent: str = "",
    drug_names: list[str] | None = None,
    all_known_drugs: list[str] | None = None,
    extract_drug_names_fn: Callable[[str], list[str]] | None = None,
) -> dict:
    """
    Parse Claude output into a StructuredAnswer-compatible dict.

    Returns a dict matching the StructuredAnswer pydantic model fields.
    """
    result: dict = {
        "verdict": "CONSULT_PHARMACIST",
        "answer": "", "short_answer": "", "warning": "",
        "details": [], "action": [], "article": "",
        "direct": "", "do": [], "avoid": [], "doctor": [],
        "raw": answer_text or "", "confidence": "MEDIUM", "sources": "",
        "interaction_summary": {"avoid_pairs": [], "caution_pairs": []},
        "citations": [], "intent": "general", "retrieval_status": "LABEL_NOT_FOUND",
        "drug": "", "common_side_effects": "", "mechanism": "",
        "serious_side_effects": [], "warning_signs": [], "pubmed_studies": [],
        "generic_name": "", "brand_names": [], "side_effects_data": {},
        "boxed_warnings": [], "mechanism_of_action": {}, "structured_sources": [],
    }

    if not answer_text:
        return result

    try:
        text = post_process_cached_answer(answer_text.strip())
        result["raw"] = text

        def _normalize_verdict(value: str) -> str:
            upper = value.strip().upper().replace("-", " ").replace("_", " ")
            upper = re.sub(r"\s+", " ", upper)
            if upper.startswith("AVOID") or upper.startswith("NO"):
                return "AVOID"
            if upper.startswith("CAUTION") or upper.startswith("MAYBE") or upper.startswith("DEPENDS"):
                return "CAUTION"
            if upper.startswith("SAFE") or upper.startswith("YES") or upper.startswith("USUALLY YES"):
                return "SAFE"
            if upper.startswith("CONSULT") or upper.startswith("NEEDS REVIEW") or upper.startswith("ASK"):
                return "CONSULT_PHARMACIST"
            return ""

        label_boundary = r"(?=^\s*(?:VERDICT|ANSWER|DIRECT|WHY|DO|AVOID|DOCTOR|WARNING|DETAILS|ACTION|ARTICLE|GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF|SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?|CONFIDENCE|SOURCES)\s*[:\-]|\Z)"

        def _extract_field(patterns: list[str]) -> str:
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
                if match:
                    value = (match.group(1) or "").strip()
                    if value:
                        return value
            return ""

        def _split_items(value: str) -> list[str]:
            if not value:
                return []
            normalized = value.replace("\r\n", "\n").strip()
            parts = [normalized]
            if "|" in normalized:
                parts = normalized.split("|")
            elif "\n" in normalized:
                parts = normalized.split("\n")
            cleaned: list[str] = []
            for part in parts:
                item = re.sub(r"^\s*[-*•]+\s*", "", part).strip(" .;")
                if item:
                    cleaned.append(item)
            return list(dict.fromkeys(cleaned))

        verdict_raw = _extract_field([rf"^\s*VERDICT\s*[:\-]\s*(.+?)\s*{label_boundary}"])
        result["verdict"] = _normalize_verdict(verdict_raw) or extract_verdict(
            text, question, query_intent, drug_names, all_known_drugs,
        )

        answer_raw = _extract_field([
            rf"^\s*ANSWER\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DIRECT\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        warning_raw = _extract_field([
            rf"^\s*WARNING\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DOCTOR\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        details_raw = _extract_field([rf"^\s*DETAILS\s*[:\-]\s*(.+?)\s*{label_boundary}"])
        action_raw = _extract_field([
            rf"^\s*ACTION\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*DO\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        article_raw = _extract_field([
            rf"^\s*ARTICLE\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*WHY\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*REASON\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        do_raw = _extract_field([rf"^\s*DO\s*[:\-]\s*(.+?)\s*{label_boundary}"])
        avoid_raw = _extract_field([rf"^\s*AVOID\s*[:\-]\s*(.+?)\s*{label_boundary}"])
        doctor_raw = _extract_field([
            rf"^\s*DOCTOR\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*GET\s+MEDICAL\s+HELP(?:\s+NOW)?\s+IF\s*[:\-]\s*(.+?)\s*{label_boundary}",
            rf"^\s*SEEK\s+MEDICAL\s+HELP(?:\s+NOW)?(?:\s+IF)?\s*[:\-]\s*(.+?)\s*{label_boundary}",
        ])
        confidence_raw = _extract_field([rf"^\s*CONFIDENCE\s*[:\-]\s*(.+?)\s*{label_boundary}"])
        sources_raw = _extract_field([rf"^\s*SOURCES?\s*[:\-]\s*(.+?)\s*{label_boundary}"])

        result["answer"] = answer_raw or ""
        result["short_answer"] = answer_raw or ""
        result["direct"] = answer_raw or ""
        result["warning"] = warning_raw or ""
        result["details"] = _split_items(details_raw)
        result["action"] = _split_items(action_raw)
        result["article"] = article_raw or ""
        result["do"] = _split_items(do_raw or action_raw)
        result["avoid"] = _split_items(avoid_raw)
        result["doctor"] = _split_items(doctor_raw)
        result["sources"] = sources_raw or ""

        conf = confidence_raw.strip().upper()
        if conf in ("HIGH", "MEDIUM", "LOW"):
            result["confidence"] = conf
        elif "HIGH" in conf:
            result["confidence"] = "HIGH"
        elif "LOW" in conf:
            result["confidence"] = "LOW"

        if not result["answer"]:
            sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " ").strip())
            if sentences and sentences[0]:
                result["answer"] = sentences[0].strip()
                result["direct"] = result["answer"]

        if result["answer"] and not re.search(r"[.!?]$", result["answer"]):
            result["answer"] += "."
            result["direct"] = result["answer"]

        _fn = extract_drug_names_fn
        result["interaction_summary"] = build_interaction_summary(question, _fn, all_known_drugs) if _fn else {"avoid_pairs": [], "caution_pairs": []}
        result["verdict"] = validate_and_correct_verdict(
            text, result["verdict"], question,
            interaction_summary=result["interaction_summary"],
            query_intent=query_intent,
        )
        result = _format_for_ui(result, question)
        return result

    except Exception:
        fallback_text = answer_text.strip()
        result["raw"] = fallback_text
        result["verdict"] = extract_verdict(fallback_text, question, query_intent, drug_names, all_known_drugs)
        sentences = re.split(r"(?<=[.!?])\s+", fallback_text.replace("\n", " ").strip())
        if sentences and sentences[0]:
            result["direct"] = sentences[0].strip()
            if result["direct"] and not re.search(r"[.!?]$", result["direct"]):
                result["direct"] += "."
        if extract_drug_names_fn:
            result["interaction_summary"] = build_interaction_summary(question, extract_drug_names_fn, all_known_drugs)
            result["verdict"] = validate_and_correct_verdict(
                fallback_text, result["verdict"], question,
                interaction_summary=result["interaction_summary"],
                query_intent=query_intent,
            )
        result = _format_for_ui(result, question)
        return result


def _format_for_ui(result: dict, question: str = "") -> dict:
    """Remove generic noise, keep response focused."""
    generic_noise = (
        "consult your pharmacist", "ask your pharmacist", "talk to your doctor",
        "ask your doctor", "for emergencies", "seek medical attention immediately",
        "always read the label", "follow package directions",
        "follow the package directions", "use the lowest effective dose",
        "keep out of reach of children",
    )

    def _is_relevant(item: str) -> bool:
        lower = item.lower().strip()
        return bool(lower) and not any(phrase in lower for phrase in generic_noise)

    def _limit(items: list[str], count: int) -> list[str]:
        filtered = [i.strip() for i in items if _is_relevant(i)]
        return list(dict.fromkeys(filtered))[:count]

    sentences = re.split(r"(?<=[.!?])\s+", (result.get("direct") or "").strip())
    result["direct"] = " ".join([s.strip() for s in sentences if s.strip()][:2]).strip()
    result["do"] = _limit(result.get("do", []), 2)
    result["avoid"] = _limit(result.get("avoid", []), 1)
    result["doctor"] = _limit(result.get("doctor", []), 1)
    return result
