"""
RxBuddy Accuracy Test Suite
============================

Validates 106 drug queries against the live /search endpoint.

FAIL conditions:
  F1  Wrong verdict (e.g., SAFE for a known dangerous combination)
  F2  Vague primary answer (banned hedge phrases present)
  F3  Missing required keywords (wrong or missing drug info)
  F4  Forbidden phrase present (e.g., "safe to take" on warfarin+aspirin)
  F5  Warning absent when interaction/risk requires one
  F6  Verdict contradicts answer text (SAFE verdict + danger language)

Run:
  # All offline unit tests (no server):
  pytest tests/test_accuracy.py -v -m "not integration"

  # All live integration tests (requires backend on BASE_URL):
  pytest tests/test_accuracy.py -v -m integration

  # Full accuracy score report (CLI):
  python tests/test_accuracy.py

  # Save JSON report:
  python tests/test_accuracy.py --report

  # Single category:
  python tests/test_accuracy.py --category interaction_highrisk

  # Failures only:
  python tests/test_accuracy.py --fail-only
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

BASE_URL = os.environ.get("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8000").rstrip("/")

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# Mirrors _VAGUE_BANNED in main.py — keep in sync
VAGUE_PHRASES = frozenset({
    "it depends",
    "generally okay",
    "generally safe",
    "may be safe",
    "could be safe",
    "might be safe",
    "usually safe",
    "typically safe",
    "often safe",
    "in most cases",
    "for most people",
    "be careful",
})

SEVERITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class DrugTestCase:
    id: str
    query: str
    expected_verdicts: list[str]           # any one of these = verdict pass
    required_keywords: list[str] = field(default_factory=list)   # all must appear
    forbidden_phrases: list[str] = field(default_factory=list)   # none must appear
    require_warning: bool = False          # structured.warning must be non-empty
    category: str = "general"
    description: str = ""
    severity: str = "high"                 # high / medium / low (weighting)


@dataclass
class TestResult:
    test_id: str
    query: str
    category: str
    severity: str
    passed: bool
    verdict_got: str
    verdict_ok: bool
    answer_text: str
    warning_text: str
    failures: list[str]
    duration_ms: float


# ── Helper functions ───────────────────────────────────────────────────────────

def _vague_check(text: str) -> Optional[str]:
    """Return the first vague phrase found, or None."""
    lower = text.lower()
    for phrase in VAGUE_PHRASES:
        if phrase in lower:
            return phrase
    return None


def _missing_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return keywords NOT found in text (case-insensitive)."""
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() not in lower]


def _found_forbidden(text: str, forbidden: list[str]) -> list[str]:
    """Return forbidden phrases found in text (case-insensitive)."""
    lower = text.lower()
    return [ph for ph in forbidden if ph.lower() in lower]


def _build_combined_text(result: dict) -> str:
    """Concatenate all answer text fields for keyword search."""
    s = result.get("structured") or {}
    parts = [
        s.get("answer", ""),
        s.get("warning", ""),
        s.get("article", ""),
        " ".join(s.get("details") or []),
        " ".join(s.get("action") or []),
        s.get("direct", ""),        # legacy
        " ".join(s.get("do") or []),  # legacy
        result.get("answer", ""),
    ]
    return " ".join(p for p in parts if p).lower()


# ── Test corpus ────────────────────────────────────────────────────────────────

DRUG_TEST_CORPUS: list[DrugTestCase] = [

    # ══════════════════════════════════════════════════════════════════════
    # HIGH-RISK INTERACTIONS  (must be AVOID or CAUTION, never SAFE)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="HRI-001", query="can i take warfarin with aspirin",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed"],
        forbidden_phrases=["safe to take together"],
        require_warning=True, category="interaction_highrisk",
        description="Warfarin + aspirin — major bleeding risk",
    ),
    DrugTestCase(
        id="HRI-002", query="can i take Eliquis with ibuprofen",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed"],
        require_warning=True, category="interaction_highrisk",
        description="Apixaban (Eliquis) + ibuprofen — bleeding risk",
    ),
    DrugTestCase(
        id="HRI-003", query="warfarin and naproxen interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed"],
        require_warning=True, category="interaction_highrisk",
        description="Warfarin + naproxen — major bleeding risk",
    ),
    DrugTestCase(
        id="HRI-004", query="can i take Xarelto with aspirin",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed"],
        require_warning=True, category="interaction_highrisk",
        description="Rivaroxaban (Xarelto) + aspirin — bleeding risk",
    ),
    DrugTestCase(
        id="HRI-005", query="can i take tramadol with sertraline",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["serotonin"],
        require_warning=True, category="interaction_highrisk",
        description="Tramadol + sertraline — serotonin syndrome risk",
    ),
    DrugTestCase(
        id="HRI-006", query="tramadol and fluoxetine together",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["serotonin"],
        require_warning=True, category="interaction_highrisk",
        description="Tramadol + fluoxetine — serotonin syndrome",
    ),
    DrugTestCase(
        id="HRI-007", query="can i take sildenafil with nitroglycerin",
        expected_verdicts=["AVOID"],
        required_keywords=["blood pressure", "nitrate"],
        forbidden_phrases=["safe to take"],
        require_warning=True, category="interaction_highrisk",
        description="Sildenafil + nitroglycerin — fatal hypotension",
    ),
    DrugTestCase(
        id="HRI-008", query="methotrexate and ibuprofen interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["toxic", "kidney"],
        require_warning=True, category="interaction_highrisk",
        description="Methotrexate + ibuprofen — reduced clearance, toxicity",
    ),
    DrugTestCase(
        id="HRI-009", query="verapamil and metoprolol interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["heart", "block"],
        require_warning=True, category="interaction_highrisk",
        description="Verapamil + metoprolol — heart block risk",
    ),
    DrugTestCase(
        id="HRI-010", query="digoxin and amiodarone interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["digoxin", "toxic"],
        require_warning=True, category="interaction_highrisk",
        description="Digoxin + amiodarone — elevated digoxin → toxicity",
    ),
    DrugTestCase(
        id="HRI-011", query="simvastatin and gemfibrozil together",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["muscle", "myopathy"],
        require_warning=True, category="interaction_highrisk",
        description="Simvastatin + gemfibrozil — severe myopathy/rhabdomyolysis",
    ),
    DrugTestCase(
        id="HRI-012", query="ibuprofen and lisinopril interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["kidney", "blood pressure"],
        require_warning=True, category="interaction_highrisk",
        description="Ibuprofen + lisinopril — reduced antihypertensive effect, kidney risk",
    ),
    DrugTestCase(
        id="HRI-013", query="fluconazole and warfarin interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed", "warfarin"],
        require_warning=True, category="interaction_highrisk",
        description="Fluconazole + warfarin — INR elevation, bleeding",
    ),
    DrugTestCase(
        id="HRI-014", query="azithromycin and ciprofloxacin together",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["qt", "heart"],
        require_warning=True, category="interaction_highrisk",
        description="Azithromycin + ciprofloxacin — QT prolongation",
    ),
    DrugTestCase(
        id="HRI-015", query="gabapentin and oxycodone together",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["respiratory", "depress"],
        forbidden_phrases=["safe to take together"],
        require_warning=True, category="interaction_highrisk",
        description="Gabapentin + opioids — respiratory depression (FDA black box)",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # MODERATE INTERACTIONS  (CAUTION or CONSULT_PHARMACIST)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="MOD-001", query="metformin and alcohol",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["lactic", "liver", "alcohol"],
        require_warning=True, category="interaction_moderate",
        description="Metformin + alcohol — lactic acidosis risk",
    ),
    DrugTestCase(
        id="MOD-002", query="tylenol and alcohol side effects",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["liver", "alcohol"],
        forbidden_phrases=["empty stomach"],
        require_warning=True, category="interaction_moderate",
        description="Acetaminophen + alcohol — liver toxicity (relevance guard test)",
    ),
    DrugTestCase(
        id="MOD-003", query="atorvastatin and grapefruit juice",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["grapefruit", "statin"],
        require_warning=True, category="interaction_moderate",
        description="Atorvastatin + grapefruit — increased statin blood levels",
    ),
    DrugTestCase(
        id="MOD-004", query="simvastatin and amiodarone interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["muscle", "myopathy"],
        require_warning=True, category="interaction_moderate",
        description="Simvastatin + amiodarone — myopathy risk (FDA dose limit)",
    ),
    DrugTestCase(
        id="MOD-005", query="clopidogrel and omeprazole interaction",
        expected_verdicts=["CAUTION", "AVOID", "CONSULT_PHARMACIST"],
        required_keywords=["clopidogrel", "platelet"],
        category="interaction_moderate",
        description="Clopidogrel + omeprazole — CYP2C19 inhibition, reduced efficacy",
    ),
    DrugTestCase(
        id="MOD-006", query="lisinopril and potassium supplements",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["potassium", "hyperkalemia"],
        require_warning=True, category="interaction_moderate",
        description="Lisinopril + potassium — hyperkalemia risk",
    ),
    DrugTestCase(
        id="MOD-007", query="ciprofloxacin and antacids interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["absorption", "antacid"],
        category="interaction_moderate",
        description="Ciprofloxacin + antacids — chelation, reduced absorption",
    ),
    DrugTestCase(
        id="MOD-008", query="levothyroxine and calcium interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["absorption", "calcium", "levothyroxine"],
        category="interaction_moderate",
        description="Levothyroxine + calcium — reduced absorption (separate by 4h)",
    ),
    DrugTestCase(
        id="MOD-009", query="insulin and alcohol interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["blood sugar", "hypoglycemia"],
        require_warning=True, category="interaction_moderate",
        description="Insulin + alcohol — hypoglycemia masking risk",
    ),
    DrugTestCase(
        id="MOD-010", query="prednisone and ibuprofen together",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["stomach", "bleed", "ulcer"],
        require_warning=True, category="interaction_moderate",
        description="Prednisone + ibuprofen — additive GI bleeding risk",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # SAFE COMBINATIONS  (should be SAFE or at most CAUTION)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="SAFE-001", query="can i take atorvastatin with lisinopril",
        expected_verdicts=["SAFE"],
        required_keywords=["atorvastatin", "lisinopril"],
        forbidden_phrases=["avoid", "dangerous"],
        require_warning=False, category="safe_combination",
        description="Atorvastatin + lisinopril — no clinically significant interaction",
    ),
    DrugTestCase(
        id="SAFE-002", query="metformin and lisinopril interaction",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["metformin", "lisinopril"],
        forbidden_phrases=["avoid taking together"],
        category="safe_combination",
        description="Metformin + lisinopril — generally safe combination",
    ),
    DrugTestCase(
        id="SAFE-003", query="amlodipine and atorvastatin together",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["amlodipine", "atorvastatin"],
        category="safe_combination",
        description="Amlodipine + atorvastatin — safe at standard doses",
        severity="medium",
    ),
    DrugTestCase(
        id="SAFE-004", query="can i take omeprazole with metformin",
        expected_verdicts=["SAFE"],
        required_keywords=["omeprazole", "metformin"],
        category="safe_combination",
        description="Omeprazole + metformin — no significant interaction",
        severity="medium",
    ),
    DrugTestCase(
        id="SAFE-005", query="can i take tylenol with ibuprofen",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["acetaminophen", "ibuprofen"],
        forbidden_phrases=["emergency", "call 911"],
        category="safe_combination",
        description="Acetaminophen + ibuprofen — generally combinable with care",
        severity="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # SIDE EFFECTS  (any non-AVOID verdict OK; must mention key effect)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="SE-001", query="lisinopril side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["cough"],
        category="side_effects",
        description="Lisinopril — dry cough is hallmark side effect",
    ),
    DrugTestCase(
        id="SE-002", query="atorvastatin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["muscle"],
        category="side_effects",
        description="Atorvastatin — muscle pain / statin myopathy",
    ),
    DrugTestCase(
        id="SE-003", query="metformin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["nausea", "stomach", "diarrhea"],
        category="side_effects",
        description="Metformin — GI side effects (nausea, diarrhea)",
    ),
    DrugTestCase(
        id="SE-004", query="amlodipine side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["swelling", "edema"],
        category="side_effects",
        description="Amlodipine — peripheral edema (ankle swelling)",
    ),
    DrugTestCase(
        id="SE-005", query="metoprolol side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["fatigue"],
        category="side_effects",
        description="Metoprolol — fatigue, bradycardia",
    ),
    DrugTestCase(
        id="SE-006", query="rosuvastatin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["muscle"],
        category="side_effects",
        description="Rosuvastatin (Crestor) — statin myopathy",
    ),
    DrugTestCase(
        id="SE-007", query="sertraline side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["nausea", "sexual"],
        category="side_effects",
        description="Sertraline — nausea and sexual dysfunction",
    ),
    DrugTestCase(
        id="SE-008", query="gabapentin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["dizziness", "drowsiness"],
        category="side_effects",
        description="Gabapentin — dizziness and drowsiness",
    ),
    DrugTestCase(
        id="SE-009", query="levothyroxine side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["heart"],
        category="side_effects",
        description="Levothyroxine — cardiac symptoms from excessive dose",
    ),
    DrugTestCase(
        id="SE-010", query="furosemide side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["potassium", "electrolyte"],
        category="side_effects",
        description="Furosemide — electrolyte imbalance (potassium depletion)",
    ),
    DrugTestCase(
        id="SE-011", query="prednisone side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["blood sugar"],
        category="side_effects",
        description="Prednisone — glucose elevation, immune suppression",
    ),
    DrugTestCase(
        id="SE-012", query="alprazolam side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["drowsiness", "depend"],
        require_warning=True, category="side_effects",
        description="Alprazolam (Xanax) — sedation and dependence risk",
    ),
    DrugTestCase(
        id="SE-013", query="omeprazole side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["magnesium"],
        category="side_effects",
        description="Omeprazole — hypomagnesemia (long-term use)",
        severity="medium",
    ),
    DrugTestCase(
        id="SE-014", query="spironolactone side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["potassium"],
        category="side_effects",
        description="Spironolactone — hyperkalemia risk",
    ),
    DrugTestCase(
        id="SE-015", query="amiodarone side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["thyroid", "lung"],
        require_warning=True, category="side_effects",
        description="Amiodarone — thyroid, pulmonary, liver toxicity",
    ),
    DrugTestCase(
        id="SE-016", query="verapamil side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["constipation"],
        category="side_effects",
        description="Verapamil — constipation, bradycardia, edema",
    ),
    DrugTestCase(
        id="SE-017", query="duloxetine side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["nausea"],
        category="side_effects",
        description="Duloxetine (Cymbalta) — nausea, dry mouth",
    ),
    DrugTestCase(
        id="SE-018", query="tramadol side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["nausea", "depend"],
        require_warning=True, category="side_effects",
        description="Tramadol — dependence, seizure risk at high doses",
    ),
    DrugTestCase(
        id="SE-019", query="bupropion side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["seizure"],
        require_warning=True, category="side_effects",
        description="Bupropion (Wellbutrin) — dose-dependent seizure risk",
    ),
    DrugTestCase(
        id="SE-020", query="warfarin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["bleed", "bleeding"],
        require_warning=True, category="side_effects",
        description="Warfarin — bleeding as primary side effect",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # DOSING  (usually CONSULT_PHARMACIST or CAUTION; must mention drug + numbers)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="DOS-001", query="maximum dose of tylenol per day",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["gram", "day", "acetaminophen"],
        category="dosing",
        description="Acetaminophen max dose — answer must state specific amount",
    ),
    DrugTestCase(
        id="DOS-002", query="verapamil dosing for high blood pressure",
        expected_verdicts=["CONSULT_PHARMACIST", "CAUTION"],
        required_keywords=["verapamil"],
        category="dosing",
        description="Verapamil dosing — patient-specific, requires prescriber",
    ),
    DrugTestCase(
        id="DOS-003", query="how much ibuprofen can i take per day",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["mg", "ibuprofen"],
        category="dosing",
        description="Ibuprofen max daily dose — must mention specific mg",
    ),
    DrugTestCase(
        id="DOS-004", query="digoxin safe dose range",
        expected_verdicts=["CONSULT_PHARMACIST"],
        required_keywords=["digoxin"],
        require_warning=True, category="dosing",
        description="Digoxin — narrow therapeutic index, must flag consult",
    ),
    DrugTestCase(
        id="DOS-005", query="warfarin dose adjustment",
        expected_verdicts=["CONSULT_PHARMACIST"],
        required_keywords=["warfarin", "INR"],
        require_warning=True, category="dosing",
        description="Warfarin — INR-guided dosing, always CONSULT",
    ),
    DrugTestCase(
        id="DOS-006", query="insulin glargine dosing",
        expected_verdicts=["CONSULT_PHARMACIST"],
        required_keywords=["insulin"],
        require_warning=True, category="dosing",
        description="Insulin glargine — patient-specific dosing, always CONSULT",
    ),
    DrugTestCase(
        id="DOS-007", query="how much aspirin for heart attack prevention",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["aspirin", "mg"],
        category="dosing",
        description="Low-dose aspirin — answer must mention specific dose",
    ),
    DrugTestCase(
        id="DOS-008", query="metformin starting dose for diabetes",
        expected_verdicts=["CONSULT_PHARMACIST", "CAUTION", "SAFE"],
        required_keywords=["metformin"],
        category="dosing",
        description="Metformin starting dose — kidney-function dependent",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # FOOD AND ALCOHOL INTERACTIONS
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="FA-001", query="warfarin and vitamin K foods",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["vitamin k", "vegetable"],
        require_warning=True, category="food_alcohol",
        description="Warfarin + vitamin K foods — INR instability",
    ),
    DrugTestCase(
        id="FA-002", query="grapefruit and simvastatin",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["grapefruit", "statin"],
        require_warning=True, category="food_alcohol",
        description="Grapefruit + simvastatin — CYP3A4 inhibition, elevated levels",
    ),
    DrugTestCase(
        id="FA-003", query="alcohol and metronidazole",
        expected_verdicts=["AVOID"],
        required_keywords=["alcohol", "nausea", "vomiting"],
        forbidden_phrases=["safe to drink"],
        require_warning=True, category="food_alcohol",
        description="Metronidazole + alcohol — disulfiram-like reaction (AVOID)",
    ),
    DrugTestCase(
        id="FA-004", query="alcohol and zolpidem",
        expected_verdicts=["AVOID"],
        required_keywords=["alcohol", "sedation"],
        require_warning=True, category="food_alcohol",
        description="Zolpidem + alcohol — CNS/respiratory depression",
    ),
    DrugTestCase(
        id="FA-005", query="alcohol and alprazolam",
        expected_verdicts=["AVOID"],
        required_keywords=["alcohol", "sedation"],
        forbidden_phrases=["safe to drink", "safe to take"],
        require_warning=True, category="food_alcohol",
        description="Alprazolam + alcohol — dangerous CNS depression",
    ),
    DrugTestCase(
        id="FA-006", query="alcohol and insulin",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["blood sugar", "hypoglycemia"],
        require_warning=True, category="food_alcohol",
        description="Insulin + alcohol — masks hypoglycemia symptoms",
    ),
    DrugTestCase(
        id="FA-007", query="caffeine and levothyroxine",
        expected_verdicts=["CAUTION", "SAFE"],
        required_keywords=["absorption", "levothyroxine"],
        category="food_alcohol",
        description="Caffeine + levothyroxine — absorption timing interference",
        severity="medium",
    ),
    DrugTestCase(
        id="FA-008", query="dairy and ciprofloxacin absorption",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["absorption", "calcium", "dairy"],
        category="food_alcohol",
        description="Dairy + ciprofloxacin — reduced absorption via chelation",
    ),
    DrugTestCase(
        id="FA-009", query="alcohol and sertraline",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["alcohol", "sertraline"],
        require_warning=True, category="food_alcohol",
        description="Sertraline + alcohol — worsened depression, CNS effects",
    ),
    DrugTestCase(
        id="FA-010", query="tyramine and MAO inhibitor interaction",
        expected_verdicts=["AVOID"],
        required_keywords=["tyramine", "blood pressure"],
        forbidden_phrases=["safe"],
        require_warning=True, category="food_alcohol",
        description="Tyramine + MAOIs — hypertensive crisis",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # PREGNANCY AND LACTATION
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="PRG-001", query="can i take ibuprofen while pregnant",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["trimester", "pregnant"],
        forbidden_phrases=["safe during pregnancy"],
        require_warning=True, category="pregnancy",
        description="Ibuprofen in pregnancy — contraindicated in 3rd trimester",
    ),
    DrugTestCase(
        id="PRG-002", query="is aspirin safe during pregnancy",
        expected_verdicts=["CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["aspirin", "pregnant"],
        require_warning=True, category="pregnancy",
        description="Aspirin in pregnancy — low-dose may be used, high-dose risky",
    ),
    DrugTestCase(
        id="PRG-003", query="warfarin during pregnancy",
        expected_verdicts=["AVOID"],
        required_keywords=["warfarin", "pregnant", "fetal"],
        forbidden_phrases=["safe during pregnancy"],
        require_warning=True, category="pregnancy",
        description="Warfarin in pregnancy — teratogenic, contraindicated",
    ),
    DrugTestCase(
        id="PRG-004", query="metformin while pregnant",
        expected_verdicts=["CONSULT_PHARMACIST", "CAUTION"],
        required_keywords=["metformin", "pregnant"],
        category="pregnancy",
        description="Metformin in pregnancy — used in gestational diabetes, requires supervision",
    ),
    DrugTestCase(
        id="PRG-005", query="sertraline while breastfeeding",
        expected_verdicts=["CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["breast", "sertraline"],
        category="pregnancy",
        description="Sertraline during breastfeeding — low milk transfer",
    ),
    DrugTestCase(
        id="PRG-006", query="lisinopril during pregnancy",
        expected_verdicts=["AVOID"],
        required_keywords=["lisinopril", "pregnant"],
        forbidden_phrases=["safe during pregnancy"],
        require_warning=True, category="pregnancy",
        description="Lisinopril in pregnancy — contraindicated (ACE inhibitor embryopathy)",
    ),
    DrugTestCase(
        id="PRG-007", query="can i take acetaminophen while pregnant",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["acetaminophen", "pregnant"],
        category="pregnancy",
        description="Acetaminophen in pregnancy — generally the safest OTC analgesic",
        severity="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # EMERGENCY DETECTION
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="EMG-001", query="I took too many Tylenol pills and feel sick",
        expected_verdicts=["EMERGENCY"],
        required_keywords=["911", "emergency", "poison"],
        require_warning=True, category="emergency",
        description="Acetaminophen overdose — must trigger EMERGENCY verdict",
    ),
    DrugTestCase(
        id="EMG-002", query="severe chest pain after taking medication",
        expected_verdicts=["EMERGENCY"],
        required_keywords=["911", "emergency"],
        require_warning=True, category="emergency",
        description="Chest pain — must trigger EMERGENCY verdict",
    ),
    DrugTestCase(
        id="EMG-003", query="I can not breathe after taking my medication",
        expected_verdicts=["EMERGENCY"],
        required_keywords=["911", "emergency"],
        require_warning=True, category="emergency",
        description="Respiratory distress — must trigger EMERGENCY verdict",
    ),
    DrugTestCase(
        id="EMG-004", query="can i take tylenol with ibuprofen",
        expected_verdicts=["SAFE", "CAUTION"],
        forbidden_phrases=["call 911"],
        category="emergency",
        description="Normal query — must NOT trigger EMERGENCY verdict",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # ADDITIONAL INTERACTIONS  (top-50 pharmacy drugs)
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="ADD-001", query="losartan and potassium supplements interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["potassium", "hyperkalemia", "losartan"],
        require_warning=True, category="interaction_additional",
        description="Losartan (ARB) + potassium — hyperkalemia risk",
    ),
    DrugTestCase(
        id="ADD-002", query="furosemide and lithium interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["lithium", "toxic"],
        require_warning=True, category="interaction_additional",
        description="Furosemide + lithium — sodium depletion raises lithium levels",
    ),
    DrugTestCase(
        id="ADD-003", query="escitalopram and tramadol together",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["serotonin"],
        require_warning=True, category="interaction_additional",
        description="Escitalopram + tramadol — serotonin syndrome risk",
    ),
    DrugTestCase(
        id="ADD-004", query="spironolactone and lisinopril interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["potassium", "hyperkalemia"],
        require_warning=True, category="interaction_additional",
        description="Spironolactone + lisinopril — severe hyperkalemia",
    ),
    DrugTestCase(
        id="ADD-005", query="carvedilol and verapamil interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["heart", "block"],
        require_warning=True, category="interaction_additional",
        description="Carvedilol + verapamil — heart block / severe bradycardia",
    ),
    DrugTestCase(
        id="ADD-006", query="levothyroxine and warfarin interaction",
        expected_verdicts=["CAUTION"],
        required_keywords=["bleed", "warfarin"],
        require_warning=True, category="interaction_additional",
        description="Levothyroxine + warfarin — potentiates anticoagulation",
    ),
    DrugTestCase(
        id="ADD-007", query="albuterol and metoprolol interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["bronchospasm", "beta"],
        require_warning=True, category="interaction_additional",
        description="Albuterol + metoprolol — beta-blocker blunts bronchodilation",
    ),
    DrugTestCase(
        id="ADD-008", query="empagliflozin and furosemide interaction",
        expected_verdicts=["CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["empagliflozin", "dehydration"],
        category="interaction_additional",
        description="Empagliflozin + furosemide — additive volume depletion",
        severity="medium",
    ),
    DrugTestCase(
        id="ADD-009", query="pantoprazole and clopidogrel interaction",
        expected_verdicts=["CAUTION", "AVOID", "CONSULT_PHARMACIST"],
        required_keywords=["clopidogrel", "platelet"],
        category="interaction_additional",
        description="Pantoprazole + clopidogrel — reduced antiplatelet effect",
    ),
    DrugTestCase(
        id="ADD-010", query="clonazepam and alcohol",
        expected_verdicts=["AVOID"],
        required_keywords=["alcohol", "sedation"],
        forbidden_phrases=["safe to take"],
        require_warning=True, category="interaction_additional",
        description="Clonazepam + alcohol — CNS/respiratory depression (AVOID)",
    ),
    DrugTestCase(
        id="ADD-011", query="hydrochlorothiazide and lithium interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["lithium", "sodium", "toxic"],
        require_warning=True, category="interaction_additional",
        description="HCTZ + lithium — sodium depletion raises lithium to toxic levels",
    ),
    DrugTestCase(
        id="ADD-012", query="cyclobenzaprine and alcohol",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["sedation", "alcohol"],
        require_warning=True, category="interaction_additional",
        description="Cyclobenzaprine + alcohol — additive CNS depression",
    ),
    DrugTestCase(
        id="ADD-013", query="tamsulosin and sildenafil interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["blood pressure", "tamsulosin"],
        require_warning=True, category="interaction_additional",
        description="Tamsulosin + sildenafil — additive hypotension risk",
    ),
    DrugTestCase(
        id="ADD-014", query="meloxicam and warfarin interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["bleed", "warfarin"],
        require_warning=True, category="interaction_additional",
        description="Meloxicam + warfarin — NSAID increases bleeding risk",
    ),
    DrugTestCase(
        id="ADD-015", query="trazodone and alcohol interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["sedation", "alcohol"],
        require_warning=True, category="interaction_additional",
        description="Trazodone + alcohol — enhanced sedation",
    ),
    DrugTestCase(
        id="ADD-016", query="rosuvastatin and grapefruit juice",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["rosuvastatin", "grapefruit"],
        category="interaction_additional",
        description="Rosuvastatin + grapefruit — less interaction than simvastatin",
        severity="medium",
    ),
    DrugTestCase(
        id="ADD-017", query="venlafaxine and tramadol interaction",
        expected_verdicts=["AVOID", "CAUTION"],
        required_keywords=["serotonin"],
        require_warning=True, category="interaction_additional",
        description="Venlafaxine + tramadol — serotonin syndrome risk",
    ),
    DrugTestCase(
        id="ADD-018", query="amoxicillin and alcohol interaction",
        expected_verdicts=["CAUTION", "SAFE"],
        required_keywords=["amoxicillin", "alcohol"],
        category="interaction_additional",
        description="Amoxicillin + alcohol — generally safe but reduces recovery",
        severity="low",
    ),
    DrugTestCase(
        id="ADD-019", query="montelukast and aspirin interaction",
        expected_verdicts=["SAFE", "CAUTION"],
        required_keywords=["montelukast"],
        category="interaction_additional",
        description="Montelukast + aspirin — generally safe combination",
        severity="low",
    ),
    DrugTestCase(
        id="ADD-020", query="propranolol and epinephrine interaction",
        expected_verdicts=["CAUTION", "AVOID"],
        required_keywords=["blood pressure", "epinephrine"],
        require_warning=True, category="interaction_additional",
        description="Propranolol + epinephrine — severe hypertension / reflex bradycardia",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # QUALITY AND SCHEMA VALIDATION
    # ══════════════════════════════════════════════════════════════════════

    DrugTestCase(
        id="QA-001", query="aspirin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["aspirin"],
        category="quality",
        description="Schema: answer field must be present and about aspirin",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-002", query="lisinopril what does it treat",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["lisinopril", "blood pressure"],
        category="quality",
        description="Schema: general info query returns relevant answer",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-003", query="ibuprofin side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["ibuprofen"],
        category="quality",
        description="Spell-correction: 'ibuprofin' resolves to ibuprofen",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-004", query="Lipitor side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["atorvastatin", "muscle"],
        category="quality",
        description="Brand name: Lipitor → atorvastatin",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-005", query="Crestor side effects",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["rosuvastatin"],
        category="quality",
        description="Brand name: Crestor → rosuvastatin",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-006", query="Advil and Tylenol",
        expected_verdicts=["SAFE", "CAUTION", "CONSULT_PHARMACIST"],
        required_keywords=["ibuprofen", "acetaminophen"],
        category="quality",
        description="Brand names: Advil → ibuprofen, Tylenol → acetaminophen",
        severity="medium",
    ),
    DrugTestCase(
        id="QA-007", query="xyzanol side effects",
        expected_verdicts=["CONSULT_PHARMACIST", "INSUFFICIENT_DATA"],
        category="quality",
        description="Unknown drug — must not crash; returns CONSULT",
        severity="low",
    ),
]

assert len(DRUG_TEST_CORPUS) >= 100, f"Corpus has only {len(DRUG_TEST_CORPUS)} tests — need ≥100"


# ── Test runner ────────────────────────────────────────────────────────────────

def run_single_test(tc: DrugTestCase) -> TestResult:
    """POST to /search and validate the response against the test spec."""
    start = time.time()
    failures: list[str] = []
    verdict_got = "UNKNOWN"
    verdict_ok = False
    answer_text = ""
    warning_text = ""

    if not HAS_HTTPX:
        return TestResult(
            test_id=tc.id, query=tc.query, category=tc.category,
            severity=tc.severity, passed=False,
            verdict_got="SKIP", verdict_ok=False,
            answer_text="", warning_text="",
            failures=["httpx not installed — pip install httpx"],
            duration_ms=0.0,
        )

    try:
        with httpx.Client(timeout=45) as client:
            resp = client.post(
                f"{BASE_URL}/search",
                json={"query": tc.query, "engine": "tfidf", "top_k": 5},
            )

        if resp.status_code != 200:
            return TestResult(
                test_id=tc.id, query=tc.query, category=tc.category,
                severity=tc.severity, passed=False,
                verdict_got="HTTP_ERROR", verdict_ok=False,
                answer_text="", warning_text="",
                failures=[f"F0 HTTP {resp.status_code}: {resp.text[:200]}"],
                duration_ms=(time.time() - start) * 1000,
            )

        data = resp.json()
        results = data.get("results", [])

        if not results:
            return TestResult(
                test_id=tc.id, query=tc.query, category=tc.category,
                severity=tc.severity, passed=False,
                verdict_got="NO_RESULT", verdict_ok=False,
                answer_text="", warning_text="",
                failures=["F0 No results returned"],
                duration_ms=(time.time() - start) * 1000,
            )

        result = results[0]
        structured = result.get("structured") or {}
        verdict_got = structured.get("verdict", "UNKNOWN")

        # New 5-section fields + legacy fallbacks
        answer_text = (
            structured.get("answer") or
            structured.get("direct") or
            ""
        )
        warning_text = structured.get("warning", "")
        combined = _build_combined_text(result)

        # ── F1: Wrong verdict ─────────────────────────────────────────────────
        verdict_ok = verdict_got in tc.expected_verdicts
        if not verdict_ok:
            failures.append(
                f"F1 VERDICT: expected one of {tc.expected_verdicts}, got '{verdict_got}'"
            )

        # ── F2: Vague primary answer ──────────────────────────────────────────
        if answer_text:
            vague = _vague_check(answer_text)
            if vague:
                failures.append(f"F2 VAGUE: answer contains banned phrase '{vague}'")

        # ── F3: Missing required keywords ─────────────────────────────────────
        if tc.required_keywords:
            missing = _missing_keywords(combined, tc.required_keywords)
            if missing:
                failures.append(f"F3 KEYWORDS: missing {missing} in answer")

        # ── F4: Forbidden phrase present ──────────────────────────────────────
        if tc.forbidden_phrases:
            found = _found_forbidden(combined, tc.forbidden_phrases)
            if found:
                failures.append(f"F4 FORBIDDEN: found {found} in answer")

        # ── F5: Warning required but absent ───────────────────────────────────
        if tc.require_warning and not (warning_text or "").strip():
            failures.append("F5 WARNING: required warning field is empty")

        # ── F6: Verdict contradicts answer text ───────────────────────────────
        if verdict_got == "SAFE" and answer_text:
            danger = ("avoid", "do not take", "contraindicated", "dangerous", "serious risk")
            if any(kw in answer_text.lower() for kw in danger):
                failures.append("F6 CONSISTENCY: verdict=SAFE but answer contains danger language")
        if verdict_got == "AVOID" and answer_text:
            safe_signals = ("safe to take", "no interaction", "no significant interaction")
            if any(kw in answer_text.lower() for kw in safe_signals):
                failures.append("F6 CONSISTENCY: verdict=AVOID but answer implies safe")

    except Exception as exc:
        failures.append(f"F0 EXCEPTION: {exc}")

    duration_ms = (time.time() - start) * 1000
    return TestResult(
        test_id=tc.id, query=tc.query, category=tc.category,
        severity=tc.severity,
        passed=len(failures) == 0,
        verdict_got=verdict_got,
        verdict_ok=verdict_ok,
        answer_text=answer_text,
        warning_text=warning_text,
        failures=failures,
        duration_ms=duration_ms,
    )


# ── Score calculation ──────────────────────────────────────────────────────────

def calculate_score(results: list[TestResult]) -> dict:
    total_weight = sum(SEVERITY_WEIGHTS.get(r.severity, 1) for r in results)
    passed_weight = sum(SEVERITY_WEIGHTS.get(r.severity, 1) for r in results if r.passed)
    pct = (passed_weight / total_weight * 100) if total_weight else 0.0

    by_category: dict[str, dict] = {}
    for r in results:
        cat = r.category
        if cat not in by_category:
            by_category[cat] = {"passed": 0, "total": 0}
        by_category[cat]["total"] += 1
        if r.passed:
            by_category[cat]["passed"] += 1

    failure_codes: dict[str, int] = {}
    for r in results:
        for f in r.failures:
            code = f.split(" ")[0]  # e.g. "F1", "F2"
            failure_codes[code] = failure_codes.get(code, 0) + 1

    return {
        "accuracy_pct": round(pct, 1),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "total": len(results),
        "by_category": by_category,
        "failure_codes": failure_codes,
    }


# ── Offline unit tests (no server required) ────────────────────────────────────

class TestParserQuality:
    """pytest class — runs offline, no server needed."""

    def test_new_format_fields_extracted(self):
        from main import _parse_structured_answer
        text = (
            "VERDICT: CAUTION\n"
            "ANSWER: Use caution when combining these drugs.\n"
            "WARNING: Risk of liver damage exists.\n"
            "DETAILS: Fact one | Fact two | Fact three\n"
            "ACTION: Do this first | Do that second\n"
            "ARTICLE: Here is the clinical explanation.\n"
            "CONFIDENCE: HIGH\n"
            "SOURCES: FDA label"
        )
        sa = _parse_structured_answer(text)
        assert sa.verdict == "CAUTION"
        assert "caution" in sa.answer.lower(), f"answer: {sa.answer!r}"
        assert "liver" in sa.warning.lower(), f"warning: {sa.warning!r}"
        assert len(sa.details) == 3, f"details: {sa.details}"
        assert len(sa.action) == 2, f"action: {sa.action}"
        assert "clinical" in sa.article.lower(), f"article: {sa.article!r}"

    def test_legacy_direct_parsed_as_answer(self):
        from main import _parse_structured_answer
        text = (
            "VERDICT: SAFE\n"
            "DIRECT: This combination is safe to use.\n"
            "CONFIDENCE: HIGH\n"
            "SOURCES: FDA"
        )
        sa = _parse_structured_answer(text)
        assert "safe" in sa.answer.lower(), f"answer: {sa.answer!r}"
        assert sa.direct == sa.answer

    def test_avoid_verdict_normalized(self):
        from main import _parse_structured_answer
        text = "VERDICT: AVOID\nANSWER: Do not take these together.\nCONFIDENCE: HIGH\nSOURCES: FDA"
        sa = _parse_structured_answer(text)
        assert sa.verdict == "AVOID"

    def test_vague_answer_detected(self):
        from main import _check_vague
        assert _check_vague("VERDICT: CAUTION\nANSWER: It depends on your situation.") is True
        assert _check_vague("VERDICT: CAUTION\nANSWER: Use caution with this combination.") is False
        assert _check_vague("VERDICT: SAFE\nANSWER: Usually safe for most people.") is True

    def test_vague_check_new_and_legacy(self):
        from main import _check_vague
        # New format (ANSWER:)
        assert _check_vague("ANSWER: It depends on other medications.") is True
        # Legacy format (DIRECT:)
        assert _check_vague("DIRECT: Generally okay to combine.") is True
        # Clean answer passes
        assert _check_vague("ANSWER: Warfarin and aspirin raise serious bleeding risk.") is False

    def test_details_split_by_pipe(self):
        from main import _parse_structured_answer
        text = (
            "VERDICT: CAUTION\n"
            "ANSWER: Watch for interactions.\n"
            "DETAILS: Drug A increases toxicity | Drug B reduces clearance | Combined risk is high\n"
            "ACTION: Monitor labs | Reduce dose | See doctor\n"
            "ARTICLE: Short explanation here.\n"
            "CONFIDENCE: MEDIUM\n"
            "SOURCES: DailyMed"
        )
        sa = _parse_structured_answer(text)
        assert len(sa.details) == 3
        assert len(sa.action) == 3

    def test_emergency_verdict_passthrough(self):
        from main import _parse_structured_answer
        text = (
            "VERDICT: EMERGENCY\n"
            "ANSWER: This is a medical emergency — call 911 immediately.\n"
            "WARNING: Life-threatening situation.\n"
            "DETAILS: Call emergency services | Do not wait\n"
            "ACTION: Call 911 | Contact Poison Control at 1-800-222-1222\n"
            "ARTICLE: Overdose is a life-threatening emergency.\n"
            "CONFIDENCE: HIGH\n"
            "SOURCES: Emergency Services"
        )
        sa = _parse_structured_answer(text)
        assert sa.verdict == "EMERGENCY"
        assert "911" in sa.answer

    def test_corpus_has_minimum_100_cases(self):
        assert len(DRUG_TEST_CORPUS) >= 100

    def test_corpus_ids_unique(self):
        ids = [tc.id for tc in DRUG_TEST_CORPUS]
        assert len(ids) == len(set(ids)), "Duplicate test IDs found"

    def test_high_risk_pairs_unit(self):
        from answer_engine import check_high_risk_pair
        assert check_high_risk_pair(["warfarin", "aspirin"]) is not None
        assert check_high_risk_pair(["apixaban", "ibuprofen"]) is not None
        assert check_high_risk_pair(["metformin", "lisinopril"]) is None

    def test_emergency_detection_unit(self):
        from answer_engine import detect_emergency
        assert detect_emergency("I took too many pills and can't breathe") is True
        assert detect_emergency("severe chest pain after my medication") is True
        assert detect_emergency("can i take tylenol with ibuprofen") is False

    def test_classify_intent_unit(self):
        from answer_engine import classify_intent
        assert classify_intent("can i take warfarin with aspirin") == "interaction"
        assert classify_intent("lisinopril side effects") == "side_effects"
        assert classify_intent("can i take ibuprofen while pregnant") == "pregnancy_lactation"
        assert classify_intent("maximum dose of tylenol per day") == "dosing"


# ── pytest integration tests (require live backend) ───────────────────────────

@pytest.mark.integration
@pytest.mark.parametrize(
    "tc",
    DRUG_TEST_CORPUS,
    ids=[t.id for t in DRUG_TEST_CORPUS],
)
def test_drug_query_accuracy(tc: DrugTestCase):
    """Live integration test — requires backend running on BASE_URL."""
    if not HAS_HTTPX:
        pytest.skip("httpx not installed — pip install httpx")

    result = run_single_test(tc)

    if not result.passed:
        failure_lines = "\n".join(f"  • {f}" for f in result.failures)
        pytest.fail(
            f"\n{'─'*60}\n"
            f"TEST:    {tc.id} — {tc.description}\n"
            f"QUERY:   {tc.query}\n"
            f"VERDICT: {result.verdict_got} (expected: {tc.expected_verdicts})\n"
            f"ANSWER:  {result.answer_text[:120]!r}\n"
            f"FAILURES:\n{failure_lines}\n"
            f"{'─'*60}"
        )


# ── CLI score runner ──────────────────────────────────────────────────────────

def _print_score_report(score: dict, results: list[TestResult], fail_only: bool) -> None:
    pct = score["accuracy_pct"]
    bar_len = 30
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(f"\n{'═'*62}")
    print(f"  ACCURACY SCORE:  {pct:.1f}%  [{bar}]")
    print(f"  Passed: {score['passed']}   Failed: {score['failed']}   Total: {score['total']}")
    print(f"{'═'*62}\n")

    # Category breakdown
    print("  By category:")
    for cat, stats in sorted(score["by_category"].items()):
        cat_pct = stats["passed"] / stats["total"] * 100
        cat_fill = int(cat_pct / 100 * 20)
        cat_bar = "█" * cat_fill + "░" * (20 - cat_fill)
        flag = "  " if cat_pct == 100 else "⚠ "
        print(f"  {flag}{cat:<28} [{cat_bar}] {cat_pct:.0f}% ({stats['passed']}/{stats['total']})")

    # Failure code summary
    if score["failure_codes"]:
        print("\n  Failure breakdown:")
        code_labels = {
            "F1": "Wrong verdict",
            "F2": "Vague answer",
            "F3": "Missing keywords",
            "F4": "Forbidden phrase",
            "F5": "Missing warning",
            "F6": "Verdict/answer mismatch",
            "F0": "HTTP/exception error",
        }
        for code, count in sorted(score["failure_codes"].items()):
            label = code_labels.get(code, code)
            print(f"    {code} {label:<28} × {count}")

    # Failed tests detail
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n  {'─'*58}")
        print(f"  FAILED TESTS ({len(failed)}):")
        print(f"  {'─'*58}")
        for r in failed:
            print(f"\n  [{r.test_id}] {r.query}")
            print(f"    Verdict: {r.verdict_got}")
            if r.answer_text:
                print(f"    Answer:  {r.answer_text[:100]!r}")
            for f in r.failures:
                print(f"    → {f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="RxBuddy Accuracy Test Runner")
    parser.add_argument("--report", action="store_true", help="Save JSON report to tests/accuracy_reports/")
    parser.add_argument("--category", help="Filter by category (e.g. interaction_highrisk)")
    parser.add_argument("--fail-only", action="store_true", help="Print only failed tests during run")
    parser.add_argument("--limit", type=int, default=0, help="Run only first N tests (0 = all)")
    parser.add_argument("--id", help="Run a single test by ID (e.g. HRI-001)")
    args = parser.parse_args()

    corpus = list(DRUG_TEST_CORPUS)
    if args.id:
        corpus = [tc for tc in corpus if tc.id == args.id]
        if not corpus:
            print(f"No test with id='{args.id}' found.")
            return
    if args.category:
        corpus = [tc for tc in corpus if tc.category == args.category]
    if args.limit:
        corpus = corpus[:args.limit]

    print(f"\n  RxBuddy Accuracy Test Suite — {len(corpus)} tests → {BASE_URL}")
    print(f"  {'─'*58}")

    results: list[TestResult] = []
    for i, tc in enumerate(corpus, 1):
        result = run_single_test(tc)
        results.append(result)

        status = "✓" if result.passed else "✗"
        duration = f"{result.duration_ms:.0f}ms"
        print(f"  {status} [{i:3d}/{len(corpus)}] {tc.id:<10} {duration:>6}  {tc.query[:50]}")

        if not result.passed and not args.fail_only:
            for f in result.failures:
                print(f"              → {f}")

    score = calculate_score(results)
    _print_score_report(score, results, args.fail_only)

    if args.report:
        os.makedirs("tests/accuracy_reports", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        report_path = os.path.join("tests", "accuracy_reports", f"{ts}.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "timestamp": ts,
                    "base_url": BASE_URL,
                    "score": score,
                    "results": [asdict(r) for r in results],
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )
        print(f"  Report saved → {report_path}\n")

    # Exit non-zero when any test fails (useful for CI)
    raise SystemExit(0 if score["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
