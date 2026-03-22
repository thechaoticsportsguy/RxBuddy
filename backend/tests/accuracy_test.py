#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RxBuddy Backend Accuracy Test
==============================

Standalone script — hits the live /search endpoint and scores every response.

Six checks per query:
  C1  VERDICT PRESENT    — verdict field exists and is a known value
  C2  VERDICT CONSISTENT — AVOID/CAUTION answers contain warning language;
                           SAFE answers do not contradict with danger language
  C3  QUERY RELEVANCE    — at least one drug/concept word from the query
                           appears in the answer (catches "wrong answer" bugs)
  C4  NOT VAGUE          — primary answer is >= 40 characters
  C5  WHY PRESENT        — explanation / details / article field is non-empty
  C6  NO EMPTY ANSWER    — answer field is not null, empty, or "undefined"

  (Bonus) LATENCY        — response time <= limit (default 8s, set with --timeout)

Usage:
  python backend/tests/accuracy_test.py --url https://web-production-345e7.up.railway.app
  python backend/tests/accuracy_test.py --url http://localhost:8000
  python backend/tests/accuracy_test.py --url http://localhost:8000 --timeout 20  # allow 20s for Railway
  python backend/tests/accuracy_test.py --url http://localhost:8000 --id 12       # single query

Regression:
  Results are saved to backend/tests/results/accuracy_YYYY-MM-DD_HH-MM.json
  If the score drops >5 ppts from the previous run: REGRESSION DETECTED
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

RESULTS_DIR = Path(__file__).parent / "results"

# All verdict values the backend may return
VALID_VERDICTS = frozenset({
    "SAFE", "CAUTION", "AVOID", "CONSULT_PHARMACIST",
    "INSUFFICIENT_DATA", "EMERGENCY",
})

# Verdicts that should be accompanied by warning language
WARNING_VERDICTS = frozenset({"AVOID", "EMERGENCY"})

# Language that must appear in AVOID/EMERGENCY answers
WARNING_LANGUAGE = frozenset({
    "avoid", "dangerous", "do not", "risk", "harm", "serious",
    "contraindicated", "bleed", "fatal", "toxic", "emergency",
    "do not take", "not safe", "stop", "death",
})

# Language that must NOT appear in SAFE answers without a qualifier
DANGER_LANGUAGE = frozenset({
    "do not take", "contraindicated", "dangerous", "avoid taking",
    "serious risk", "fatal", "life-threatening",
})

# Query stop-words for relevance check
_STOP = frozenset({
    "can", "i", "take", "is", "it", "with", "and", "the", "a", "an",
    "while", "during", "for", "my", "of", "in", "to", "how", "much",
    "per", "day", "safe", "every", "on", "at", "half", "long", "term",
    "what", "are", "does", "should", "will", "get", "be", "do",
    "effects", "side", "sudden", "cut", "expired", "habit", "forming",
    "stop", "drink", "taking", "starting", "typical", "typical", "vs",
})

# Brand name -> generic synonyms for C3 relevance check.
# If the query contains a brand name and the answer mentions the generic (or vice versa),
# the query is still considered relevant.
_BRAND_GENERIC: dict[str, list[str]] = {
    "ozempic":    ["semaglutide", "glp-1", "glp1"],
    "wegovy":     ["semaglutide", "glp-1", "glp1"],
    "rybelsus":   ["semaglutide", "glp-1", "glp1"],
    "trulicity":  ["dulaglutide", "glp-1", "glp1"],
    "victoza":    ["liraglutide", "glp-1", "glp1"],
    "jardiance":  ["empagliflozin", "sglt2"],
    "farxiga":    ["dapagliflozin", "sglt2"],
    "invokana":   ["canagliflozin", "sglt2"],
    "eliquis":    ["apixaban", "anticoagulant", "blood thinner"],
    "xarelto":    ["rivaroxaban", "anticoagulant", "blood thinner"],
    "brilinta":   ["ticagrelor", "antiplatelet"],
    "entresto":   ["sacubitril", "valsartan"],
    "humira":     ["adalimumab", "tnf", "biologic"],
    "keytruda":   ["pembrolizumab", "immunotherapy"],
    "dupixent":   ["dupilumab", "biologic"],
    "skyrizi":    ["risankizumab", "biologic"],
    "rinvoq":     ["upadacitinib", "jak"],
    "cosentyx":   ["secukinumab", "biologic"],
    "taltz":      ["ixekizumab", "biologic"],
    "stelara":    ["ustekinumab", "biologic"],
    "enbrel":     ["etanercept", "tnf", "biologic"],
    "remicade":   ["infliximab", "tnf", "biologic"],
    "norco":      ["hydrocodone", "opioid"],
    "vicodin":    ["hydrocodone", "opioid"],
    "percocet":   ["oxycodone", "opioid"],
    "oxycontin":  ["oxycodone", "opioid"],
    "zoloft":     ["sertraline", "ssri"],
    "prozac":     ["fluoxetine", "ssri"],
    "lexapro":    ["escitalopram", "ssri"],
    "paxil":      ["paroxetine", "ssri"],
    "effexor":    ["venlafaxine", "snri"],
    "wellbutrin": ["bupropion"],
    "abilify":    ["aripiprazole"],
    "seroquel":   ["quetiapine"],
    "risperdal":  ["risperidone"],
    "zyprexa":    ["olanzapine"],
    "xanax":      ["alprazolam", "benzodiazepine"],
    "ativan":     ["lorazepam", "benzodiazepine"],
    "klonopin":   ["clonazepam", "benzodiazepine"],
    "valium":     ["diazepam", "benzodiazepine"],
    "ambien":     ["zolpidem"],
    "lunesta":    ["eszopiclone"],
    "adderall":   ["amphetamine", "stimulant"],
    "ritalin":    ["methylphenidate", "stimulant"],
    "vyvanse":    ["lisdexamfetamine", "stimulant"],
    "concerta":   ["methylphenidate", "stimulant"],
    "synthroid":  ["levothyroxine", "thyroid"],
    "lipitor":    ["atorvastatin", "statin"],
    "crestor":    ["rosuvastatin", "statin"],
    "zocor":      ["simvastatin", "statin"],
    "pravachol":  ["pravastatin", "statin"],
    "norvasc":    ["amlodipine", "calcium channel"],
    "prinivil":   ["lisinopril", "ace inhibitor"],
    "zestril":    ["lisinopril", "ace inhibitor"],
    "lopressor":  ["metoprolol", "beta blocker"],
    "toprol":     ["metoprolol", "beta blocker"],
    "tenormin":   ["atenolol", "beta blocker"],
    "lasix":      ["furosemide", "diuretic"],
    "aldactone":  ["spironolactone", "diuretic"],
    "coumadin":   ["warfarin", "anticoagulant"],
    "plavix":     ["clopidogrel", "antiplatelet"],
    "nexium":     ["esomeprazole", "ppi"],
    "prilosec":   ["omeprazole", "ppi"],
    "prevacid":   ["lansoprazole", "ppi"],
    "pepcid":     ["famotidine", "h2 blocker"],
    "zantac":     ["ranitidine", "h2 blocker"],
    "benadryl":   ["diphenhydramine", "antihistamine"],
    "claritin":   ["loratadine", "antihistamine"],
    "zyrtec":     ["cetirizine", "antihistamine"],
    "allegra":    ["fexofenadine", "antihistamine"],
    "tylenol":    ["acetaminophen", "paracetamol"],
    "advil":      ["ibuprofen", "nsaid"],
    "motrin":     ["ibuprofen", "nsaid"],
    "aleve":      ["naproxen", "nsaid"],
    "celebrex":   ["celecoxib", "nsaid"],
    "viagra":     ["sildenafil", "pde5"],
    "cialis":     ["tadalafil", "pde5"],
    "levitra":    ["vardenafil", "pde5"],
}

MAX_LATENCY_S = 8.0
MIN_ANSWER_LEN = 40

# ── Test queries ───────────────────────────────────────────────────────────────

QUERIES: list[str] = [
    # ── Drug interactions ──────────────────────────────────────────────────────
    "can i take eliquis with ibuprofen",
    "warfarin and aspirin interaction",
    "metformin and alcohol",
    "tylenol and alcohol side effects",
    "lisinopril and ibuprofen",
    "sertraline and alcohol",
    "can i take melatonin with zoloft",
    "adderall and caffeine",
    "can i take benadryl with nyquil",
    "insulin and alcohol",
    "sildenafil and nitrates",
    "fluoxetine and tramadol",
    "clopidogrel and omeprazole",
    "amoxicillin and alcohol",
    "ciprofloxacin and dairy",
    "levothyroxine and calcium",
    "atorvastatin and grapefruit",
    "rosuvastatin and alcohol",
    "verapamil and simvastatin",
    "digoxin and amiodarone",

    # ── Pregnancy / breastfeeding ─────────────────────────────────────────────
    "can i take ibuprofen while pregnant",
    "is tylenol safe during pregnancy",
    "can i take benadryl while breastfeeding",
    "is melatonin safe while pregnant",
    "metformin during pregnancy",

    # ── Dosing ────────────────────────────────────────────────────────────────
    "verapamil dosing",
    "how much tylenol can i take per day",
    "metformin starting dose",
    "lisinopril typical dose",
    "atorvastatin dosing",

    # ── Side effects ──────────────────────────────────────────────────────────
    "rosuvastatin side effects",
    "metformin side effects",
    "lisinopril side effects",
    "sertraline side effects",
    "adderall side effects",
    "prednisone side effects",
    "gabapentin side effects",
    "amlodipine side effects",
    "omeprazole long term side effects",
    "eliquis side effects",

    # ── Safety / warnings ─────────────────────────────────────────────────────
    "is it safe to take ibuprofen every day",
    "can i drink on antibiotics",
    "can i take expired medication",
    "is melatonin habit forming",
    "can i stop taking lisinopril suddenly",
    "can i cut metformin in half",
    "is it safe to take tylenol and ibuprofen together",

    # ── GLP-1 / weight-loss injections ────────────────────────────────────────
    "ozempic side effects",
    "can i drink alcohol while taking ozempic",
    "ozempic and thyroid cancer risk",
    "wegovy side effects",
    "ozempic nausea how to manage",
    "trulicity side effects",
    "trulicity and alcohol",
    "victoza side effects",
    "victoza and pancreatitis risk",
    "rybelsus side effects",
    "rybelsus dosing",

    # ── SGLT2 inhibitors ──────────────────────────────────────────────────────
    "jardiance side effects",
    "can i take jardiance with metformin",
    "jardiance and alcohol",
    "farxiga side effects",
    "farxiga urinary tract infection risk",
    "farxiga dosing",

    # ── Novel anticoagulants ───────────────────────────────────────────────────
    "xarelto side effects",
    "can i take xarelto with ibuprofen",
    "xarelto and grapefruit",
    "brilinta side effects",
    "brilinta and aspirin interaction",
    "eliquis and aspirin interaction",

    # ── Heart failure ─────────────────────────────────────────────────────────
    "entresto side effects",
    "entresto and potassium interaction",

    # ── Biologics / immunology ────────────────────────────────────────────────
    "humira side effects",
    "dupixent side effects",
    "keytruda side effects",
    "stelara side effects",

    # ── Thyroid ───────────────────────────────────────────────────────────────
    "synthroid side effects",
    "synthroid and food interactions",
    "can i take synthroid with coffee",
    "armour thyroid side effects",
    "levothyroxine and grapefruit",

    # ── Proton pump inhibitors ────────────────────────────────────────────────
    "nexium long term side effects",
    "can i take nexium every day",
    "prilosec side effects",
    "protonix and magnesium",
    "nexium and clopidogrel interaction",
    "omeprazole and levothyroxine interaction",

    # ── Sleep / sedation ─────────────────────────────────────────────────────
    "ambien side effects",
    "can i take ambien with alcohol",
    "lunesta side effects",
    "trazodone for sleep side effects",
    "can i take trazodone with melatonin",
    "quetiapine side effects",
    "quetiapine and alcohol",

    # ── Mood / psychiatry ─────────────────────────────────────────────────────
    "lithium side effects",
    "lithium and ibuprofen interaction",
    "lithium toxicity symptoms",
    "lithium and sodium levels",
    "lithium and alcohol",

    # ── Opioids / pain ────────────────────────────────────────────────────────
    "hydrocodone side effects",
    "can i take hydrocodone with ibuprofen",
    "oxycodone and alcohol",
    "can i take tramadol and tylenol together",
    "tramadol and alcohol interaction",
    "tramadol and sertraline interaction",

    # ── Nerve pain ────────────────────────────────────────────────────────────
    "pregabalin side effects",
    "pregabalin and alcohol",
    "gabapentin and opioids interaction",
    "pregabalin dosing for nerve pain",

    # ── Additional top-pharmacy interactions ──────────────────────────────────
    "warfarin and vitamin k foods",
    "metoprolol and verapamil interaction",
    "spironolactone and lisinopril interaction",
    "furosemide and lithium interaction",
    "prednisone and ibuprofen together",
    "can i take benadryl with alcohol",
    "ibuprofen and blood pressure medication",
    "simvastatin and gemfibrozil",
    "azithromycin and qt prolongation",
    "escitalopram and tramadol interaction",
]

assert len(QUERIES) >= 100, f"Only {len(QUERIES)} queries — need ≥ 100"


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    code: str       # C1, C2, C3 …
    passed: bool
    reason: str = ""


@dataclass
class QueryResult:
    idx: int
    query: str
    passed: bool
    latency_s: float
    verdict: str
    answer: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


# ── HTTP helper (no external deps) ────────────────────────────────────────────

def _post_search(base_url: str, query: str, http_timeout: float) -> tuple[dict, float]:
    """POST /search and return (parsed_json, latency_seconds)."""
    payload = json.dumps({"query": query, "engine": "tfidf", "top_k": 5}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/search",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    latency = time.time() - t0
    return json.loads(body), latency


# ── Field extractors ───────────────────────────────────────────────────────────

def _extract_fields(data: dict) -> tuple[str, str, list[str], str]:
    """
    Return (verdict, answer_text, details_list, combined_all) from API response.
    Handles both new 5-section format and legacy structured fields.
    """
    results = data.get("results") or []
    if not results:
        return "", "", [], ""

    result = results[0]
    s = result.get("structured") or {}

    verdict = s.get("verdict", "")

    # Primary answer (new format -> legacy -> raw)
    answer = (
        s.get("answer") or
        s.get("direct") or
        result.get("answer") or
        ""
    )

    # "Why" / explanation (new format -> legacy)
    article = s.get("article") or s.get("why") or ""
    details: list[str] = (
        s.get("details") or
        s.get("do") or
        []
    )
    warning = s.get("warning") or ""

    # Combined text for keyword search
    combined = " ".join(filter(None, [
        answer,
        warning,
        article,
        " ".join(details),
        " ".join(s.get("action") or s.get("avoid") or []),
        result.get("answer") or "",
    ])).lower()

    return verdict, answer, details + ([article] if article else []), combined


# ── Six checks ────────────────────────────────────────────────────────────────

def _c1_verdict_present(verdict: str) -> CheckResult:
    """C1: verdict must exist and be a recognised value."""
    if not verdict:
        return CheckResult("C1", False, "verdict field is missing or empty")
    if verdict not in VALID_VERDICTS:
        return CheckResult("C1", False, f"verdict '{verdict}' not in allowed set")
    return CheckResult("C1", True)


def _c2_verdict_consistent(verdict: str, answer: str, combined: str) -> CheckResult:
    """C2: AVOID answers must contain warning language; SAFE answers must not contain danger signals."""
    ans_lower = answer.lower()
    comb_lower = combined.lower()

    if verdict in WARNING_VERDICTS:
        has_warning = any(w in comb_lower for w in WARNING_LANGUAGE)
        if not has_warning:
            return CheckResult(
                "C2", False,
                f"verdict={verdict} but answer contains no warning language"
                f" (answer: {answer[:80]!r})",
            )

    if verdict == "SAFE":
        found_danger = [w for w in DANGER_LANGUAGE if w in ans_lower]
        if found_danger:
            return CheckResult(
                "C2", False,
                f"verdict=SAFE but answer contains: {found_danger} (answer: {answer[:80]!r})",
            )

    return CheckResult("C2", True)


def _c3_query_relevance(query: str, combined: str) -> CheckResult:
    """C3: at least one meaningful word from the query (or its generic equivalent) appears in the answer."""
    words = [
        w.lower().strip("'\".,!?")
        for w in re.split(r"\s+", query)
        if len(w) > 3 and w.lower() not in _STOP
    ]
    if not words:
        return CheckResult("C3", True)  # nothing to check

    for word in words:
        if word in combined:
            return CheckResult("C3", True)
        # Also accept generic drug name synonyms (brand name -> generic)
        for synonym in _BRAND_GENERIC.get(word, []):
            if synonym in combined:
                return CheckResult("C3", True)

    return CheckResult(
        "C3", False,
        f"none of {words} found in answer — possible wrong-answer bug",
    )


def _c4_not_vague(answer: str) -> CheckResult:
    """C4: primary answer must be >= MIN_ANSWER_LEN characters."""
    length = len(answer.strip())
    if length < MIN_ANSWER_LEN:
        return CheckResult(
            "C4", False,
            f"answer too short ({length} chars < {MIN_ANSWER_LEN}) — likely vague: {answer!r}",
        )
    return CheckResult("C4", True)


def _c5_why_present(details: list[str], combined: str) -> CheckResult:
    """
    C5: explanation must be present.
    Primary check: structured article/details/why fields.
    Fallback: if the combined answer text is substantive (>120 chars) the answer
    itself contains the explanation even if it's in old markdown format — pass.
    This prevents false failures on legacy DB rows that predate the 5-section schema.
    """
    non_empty = [d for d in details if d and d.strip()]
    if non_empty:
        return CheckResult("C5", True)
    if len(combined.strip()) > 120:
        return CheckResult("C5", True)
    return CheckResult("C5", False, "no explanation field and combined answer < 120 chars")


def _c6_no_empty_answer(answer: str) -> CheckResult:
    """C6: answer must not be null, empty, or the literal string 'undefined'."""
    stripped = (answer or "").strip()
    if not stripped or stripped.lower() in ("undefined", "null", "none", "n/a"):
        return CheckResult("C6", False, f"answer is empty or undefined: {answer!r}")
    return CheckResult("C6", True)


def _latency_check(latency_s: float, limit: float = MAX_LATENCY_S) -> CheckResult:
    """Bonus: response time must be <= limit seconds."""
    if latency_s > limit:
        return CheckResult(
            "LAT", False,
            f"response took {latency_s:.1f}s > {limit:.0f}s limit",
        )
    return CheckResult("LAT", True)


# ── Single query runner ────────────────────────────────────────────────────────

def run_query(idx: int, query: str, base_url: str, latency_limit: float = MAX_LATENCY_S) -> QueryResult:
    http_timeout = latency_limit + 5   # socket timeout > latency limit so we can report properly
    latency_s = http_timeout + 1
    verdict = answer = ""
    details: list[str] = []
    combined = ""
    http_error: Optional[str] = None

    try:
        data, latency_s = _post_search(base_url, query, http_timeout)
        verdict, answer, details, combined = _extract_fields(data)
    except urllib.error.HTTPError as e:
        http_error = f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        http_error = f"Connection error: {e.reason}"
    except TimeoutError:
        http_error = f"Timed out after {http_timeout:.0f}s"
    except Exception as e:
        http_error = f"Unexpected error: {e}"

    if http_error:
        fail_check = CheckResult("NET", False, http_error)
        return QueryResult(
            idx=idx, query=query, passed=False,
            latency_s=latency_s, verdict="ERROR", answer="",
            checks=[fail_check],
        )

    checks = [
        _c1_verdict_present(verdict),
        _c2_verdict_consistent(verdict, answer, combined),
        _c3_query_relevance(query, combined),
        _c4_not_vague(answer),
        _c5_why_present(details, combined),
        _c6_no_empty_answer(answer),
        _latency_check(latency_s, latency_limit),
    ]

    passed = all(c.passed for c in checks)
    return QueryResult(
        idx=idx, query=query, passed=passed,
        latency_s=latency_s, verdict=verdict, answer=answer,
        checks=checks,
    )


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_results(results: list[QueryResult]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pct = round(passed / total * 100, 1) if total else 0.0

    failure_counts: dict[str, int] = {}
    for r in results:
        for c in r.failures:
            failure_counts[c.code] = failure_counts.get(c.code, 0) + 1

    by_check: dict[str, dict] = {}
    check_codes = ["C1", "C2", "C3", "C4", "C5", "C6", "LAT", "NET"]
    for code in check_codes:
        failed_count = failure_counts.get(code, 0)
        by_check[code] = {
            "failed": failed_count,
            "passed": total - failed_count,
            "pass_rate": round((total - failed_count) / total * 100, 1) if total else 0.0,
        }

    return {
        "passed": passed,
        "total": total,
        "pct": pct,
        "failure_counts": failure_counts,
        "by_check": by_check,
        "avg_latency_s": round(
            sum(r.latency_s for r in results) / total if total else 0, 2
        ),
    }


# ── Regression detection ───────────────────────────────────────────────────────

def _load_last_score(results_dir: Path) -> Optional[float]:
    """Return the accuracy % from the most recent saved run, or None."""
    files = sorted(results_dir.glob("accuracy_*.json"))
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        return float(data.get("score", {}).get("pct", 0))
    except Exception:
        return None


def save_results(results: list[QueryResult], score: dict, base_url: str, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = results_dir / f"accuracy_{ts}.json"
    payload = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "url": base_url,
        "score": score,
        "results": [
            {
                "idx": r.idx,
                "query": r.query,
                "passed": r.passed,
                "latency_s": round(r.latency_s, 2),
                "verdict": r.verdict,
                "answer": r.answer[:200],
                "failures": [
                    {"code": c.code, "reason": c.reason}
                    for c in r.failures
                ],
            }
            for r in results
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


# ── Output helpers ─────────────────────────────────────────────────────────────

CHECK_LABELS = {
    "C1":  "Verdict present",
    "C2":  "Verdict consistent with answer",
    "C3":  "Query relevance (drug name in answer)",
    "C4":  "Answer length >= 40 chars",
    "C5":  "Explanation / why field present",
    "C6":  "Answer not empty",
    "LAT": f"Latency <= {MAX_LATENCY_S}s",
    "NET": "Network / HTTP error",
}


def print_run_header(base_url: str, total: int) -> None:
    print(f"\n{'='*64}")
    print(f"  RxBuddy Accuracy Test  |  {total} queries  |  {base_url}")
    print(f"{'='*64}\n")


def print_query_line(r: QueryResult, verbose: bool) -> None:
    status = "PASS" if r.passed else "FAIL"
    lat = f"{r.latency_s:.1f}s"
    line = f"  [{r.idx:3d}] {status}  {lat:>5}  {r.query[:54]}"
    print(line)
    if not r.passed and verbose:
        for c in r.failures:
            label = CHECK_LABELS.get(c.code, c.code)
            print(f"           {c.code} {label}")
            print(f"              -> {c.reason}")


def print_score_report(score: dict, results: list[QueryResult]) -> None:
    pct = score["pct"]
    bar_len = 30
    filled = int(pct / 100 * bar_len)
    filled_chars = "#" * filled + "-" * (bar_len - filled)

    print(f"\n{'='*64}")
    print(f"  ACCURACY:  {pct:.1f}%  [{filled_chars}]")
    print(f"  Passed: {score['passed']}  Failed: {score['total'] - score['passed']}  Total: {score['total']}")
    print(f"  Avg latency: {score['avg_latency_s']}s")
    print(f"{'='*64}\n")

    # Per-check breakdown
    print("  Check breakdown:")
    for code in ["C1", "C2", "C3", "C4", "C5", "C6", "LAT", "NET"]:
        stat = score["by_check"].get(code, {})
        if not stat:
            continue
        failed = stat.get("failed", 0)
        if failed == 0:
            continue
        label = CHECK_LABELS.get(code, code)
        print(f"    {code}  {label:<38} {failed} failure(s)")

    # Detailed failure list
    failed_results = [r for r in results if not r.passed]
    if failed_results:
        print(f"\n  {'-'*60}")
        print(f"  FAILURES ({len(failed_results)}):")
        print(f"  {'-'*60}")
        for r in failed_results:
            print(f"\n  [{r.idx:3d}] {r.query}")
            print(f"        verdict: {r.verdict}")
            print(f"        answer:  {r.answer[:100]!r}")
            for c in r.failures:
                label = CHECK_LABELS.get(c.code, c.code)
                print(f"        {c.code} FAIL - {label}")
                print(f"               {c.reason}")

    # Grouped by fail type
    if score["failure_counts"]:
        print(f"\n  {'-'*60}")
        print("  Failures grouped by check:")
        for code, count in sorted(score["failure_counts"].items()):
            label = CHECK_LABELS.get(code, code)
            print(f"    {code}  {label:<38} x{count}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RxBuddy backend accuracy test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python backend/tests/accuracy_test.py --url https://web-production-345e7.up.railway.app\n"
            "  python backend/tests/accuracy_test.py --url http://localhost:8000\n"
            "  python backend/tests/accuracy_test.py --url http://localhost:8000 --id 5\n"
            "  python backend/tests/accuracy_test.py --url http://localhost:8000 --no-save\n"
        ),
    )
    parser.add_argument(
        "--url", required=True,
        help="Base URL of the backend (e.g. http://localhost:8000)",
    )
    parser.add_argument(
        "--id", type=int, default=0,
        help="Run only query at this 1-based index",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Do not write a results JSON file",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only print failures and final score",
    )
    parser.add_argument(
        "--timeout", type=float, default=MAX_LATENCY_S,
        help=f"Latency limit in seconds — responses slower than this fail the LAT check (default: {MAX_LATENCY_S}s)",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    latency_limit = args.timeout
    corpus = QUERIES

    if args.id:
        if args.id < 1 or args.id > len(corpus):
            print(f"--id must be between 1 and {len(corpus)}", file=sys.stderr)
            sys.exit(1)
        corpus = [corpus[args.id - 1]]
        offset = args.id - 1
    else:
        offset = 0

    # Update LAT label to reflect actual configured limit
    CHECK_LABELS["LAT"] = f"Latency <= {latency_limit}s"

    print_run_header(base_url, len(corpus))

    # Check for previous score before running (for regression comparison)
    last_score: Optional[float] = None
    if not args.no_save and not args.id:
        last_score = _load_last_score(RESULTS_DIR)

    results: list[QueryResult] = []
    for i, query in enumerate(corpus, start=1):
        real_idx = i + offset
        r = run_query(real_idx, query, base_url, latency_limit=latency_limit)
        results.append(r)
        if not args.quiet or not r.passed:
            print_query_line(r, verbose=not args.quiet)

    score = score_results(results)
    print_score_report(score, results)

    # Regression detection
    if last_score is not None:
        drop = last_score - score["pct"]
        if drop > 5.0:
            print(
                f"  *** REGRESSION DETECTED ***\n"
                f"      Previous score: {last_score:.1f}%\n"
                f"      Current score:  {score['pct']:.1f}%\n"
                f"      Drop: {drop:.1f} percentage points (threshold: 5.0)\n"
            )
        elif drop > 0:
            print(f"  Score vs last run: {last_score:.1f}% -> {score['pct']:.1f}% ({drop:+.1f} pts)")
        else:
            print(f"  Score vs last run: {last_score:.1f}% -> {score['pct']:.1f}% ({-drop:+.1f} pts)")

    # Save results
    if not args.no_save and not args.id:
        out_path = save_results(results, score, base_url, RESULTS_DIR)
        print(f"  Results saved -> {out_path}\n")

    # Exit code for CI integration
    sys.exit(0 if score["pct"] >= 70.0 else 1)


if __name__ == "__main__":
    main()
