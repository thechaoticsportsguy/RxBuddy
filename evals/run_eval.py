"""Score the production RxBuddy /answer endpoint against the gold set.

Reads `evals/drug_qa_goldset.csv`, calls the production API for each
question, and uses Claude Haiku as a judge with a strict JSON rubric.
Writes a timestamped results CSV and prints one summary line.

Env vars (read from `.env` via python-dotenv):
    ANTHROPIC_API_KEY   required — for the judge
    RXBUDDY_API_URL     base URL (default: https://rxbuddy.fly.dev)
    RXBUDDY_API_PATH    endpoint path (default: /answer)

Exit codes:
    0 — at least one row scored successfully
    1 — every row failed
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from tqdm.asyncio import tqdm_asyncio

EVALS_DIR = Path(__file__).resolve().parent
GOLDSET_PATH = EVALS_DIR / "drug_qa_goldset.csv"

JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_SYSTEM_PROMPT = (
    "You are an evaluator for a medical Q&A system. You will be given a "
    "patient question, a list of expected keywords, the expected citation "
    "source, and the model's response. Score strictly. Reply ONLY with "
    "valid JSON in this schema:\n"
    "{\n"
    '  "keyword_coverage": <float 0.0 to 1.0, fraction of expected keywords '
    "present in the response>,\n"
    '  "hallucination_present": <true if the response contains medical claims '
    "NOT supported by the expected source, else false>,\n"
    '  "citation_correct": <true if the response cites the expected source, '
    "else false>,\n"
    '  "reasoning": "<one sentence>"\n'
    "}"
)

RESULT_COLUMNS = [
    "id",
    "drug",
    "question",
    "category",
    "keyword_coverage",
    "hallucination_present",
    "citation_correct",
    "judge_reasoning",
    "raw_response",
    "timestamp",
]

logging.basicConfig(
    level=os.getenv("EVAL_LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("rxbuddy.eval")


def _load_goldset() -> list[dict[str, str]]:
    if not GOLDSET_PATH.exists():
        raise FileNotFoundError(f"goldset not found: {GOLDSET_PATH}")
    with GOLDSET_PATH.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def _ask_rxbuddy(
    client: httpx.AsyncClient, base_url: str, path: str, question: str
) -> str:
    """POST the question to the API. Retry up to 3x with exponential backoff."""
    url = base_url.rstrip("/") + path
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(
                url,
                json={"question": question},
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # /answer returns {"question", "answer"}; /v2/search returns
            # the structured search response. Handle both shapes.
            if isinstance(data, dict):
                if "answer" in data and isinstance(data["answer"], str):
                    return data["answer"]
                if "results" in data and data["results"]:
                    first = data["results"][0]
                    if isinstance(first, dict):
                        return str(first.get("answer") or json.dumps(first))
            return json.dumps(data)
        except Exception as exc:
            last_exc = exc
            backoff = 2**attempt
            log.warning("API call failed (attempt %d): %s — backing off %ds", attempt + 1, exc, backoff)
            await asyncio.sleep(backoff)
    assert last_exc is not None
    raise last_exc


async def _judge(
    client: AsyncAnthropic,
    question: str,
    expected_keywords: str,
    expected_source: str,
    model_response: str,
) -> dict[str, Any]:
    """Call Claude Haiku as judge. Returns parsed JSON dict, or raises."""
    user_msg = (
        f"PATIENT QUESTION:\n{question}\n\n"
        f"EXPECTED KEYWORDS (pipe-separated, all should appear):\n{expected_keywords}\n\n"
        f"EXPECTED CITATION SOURCE:\n{expected_source}\n\n"
        f"MODEL RESPONSE:\n{model_response}\n\n"
        "Return your JSON evaluation now."
    )
    resp = await client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=512,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text if resp.content else ""
    # Trim accidental code fences.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return json.loads(cleaned)


async def _score_row(
    row: dict[str, str],
    http_client: httpx.AsyncClient,
    judge_client: AsyncAnthropic,
    base_url: str,
    api_path: str,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: dict[str, Any] = {
        "id": row["id"],
        "drug": row["drug"],
        "question": row["question"],
        "category": row["category"],
        "keyword_coverage": None,
        "hallucination_present": None,
        "citation_correct": None,
        "judge_reasoning": "",
        "raw_response": "",
        "timestamp": timestamp,
    }
    try:
        response_text = await _ask_rxbuddy(http_client, base_url, api_path, row["question"])
        out["raw_response"] = response_text
    except Exception as exc:
        out["judge_reasoning"] = f"API call failed after retries: {exc}"
        return out

    try:
        judgment = await _judge(
            judge_client,
            row["question"],
            row["expected_answer_keywords"],
            row["expected_citation_source"],
            response_text,
        )
        out["keyword_coverage"] = float(judgment.get("keyword_coverage", 0.0))
        out["hallucination_present"] = bool(judgment.get("hallucination_present", False))
        out["citation_correct"] = bool(judgment.get("citation_correct", False))
        out["judge_reasoning"] = str(judgment.get("reasoning", ""))[:500]
    except Exception as exc:
        out["judge_reasoning"] = f"Judge call/parse failed: {exc}"
    return out


def _summarize(results: list[dict[str, Any]]) -> tuple[float, float, float, int]:
    scored = [r for r in results if r["keyword_coverage"] is not None]
    if not scored:
        return 0.0, 0.0, 0.0, 0
    n = len(scored)
    accuracy = sum(r["keyword_coverage"] for r in scored) / n * 100.0
    hallucination_rate = sum(1 for r in scored if r["hallucination_present"]) / n * 100.0
    citation_rate = sum(1 for r in scored if r["citation_correct"]) / n * 100.0
    return accuracy, hallucination_rate, citation_rate, n


def _write_results(results: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in results:
            writer.writerow({col: row.get(col, "") for col in RESULT_COLUMNS})


async def _amain() -> int:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is required (set in .env)", file=sys.stderr)
        return 1

    base_url = os.getenv("RXBUDDY_API_URL", "https://rxbuddy.fly.dev").strip()
    api_path = os.getenv("RXBUDDY_API_PATH", "/answer").strip() or "/answer"
    if not api_path.startswith("/"):
        api_path = "/" + api_path

    rows = _load_goldset()
    if not rows:
        print("ERROR: goldset is empty", file=sys.stderr)
        return 1

    print(f"Eval target: {base_url}{api_path}  |  rows: {len(rows)}", file=sys.stderr)

    judge_client = AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(int(os.getenv("EVAL_CONCURRENCY", "4")))

    async with httpx.AsyncClient() as http_client:
        async def _bounded(row: dict[str, str]) -> dict[str, Any]:
            async with sem:
                return await _score_row(row, http_client, judge_client, base_url, api_path)

        results = await tqdm_asyncio.gather(
            *(_bounded(row) for row in rows),
            desc="Evaluating",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = EVALS_DIR / f"results_{timestamp}.csv"
    _write_results(results, out_path)

    accuracy, hallucination_rate, citation_rate, scored_n = _summarize(results)
    print(
        f"Accuracy: {accuracy:.1f}% | "
        f"Hallucination rate: {hallucination_rate:.1f}% | "
        f"Citation rate: {citation_rate:.1f}%"
    )
    print(f"Wrote: {out_path}  |  scored: {scored_n}/{len(rows)}", file=sys.stderr)
    return 0 if scored_n > 0 else 1


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
