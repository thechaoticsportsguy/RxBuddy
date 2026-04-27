# RxBuddy evals

Ground-truth dataset and reproducible eval pipeline for RxBuddy. Every quality claim made about RxBuddy after Phase 1 is backed by the numbers this folder produces.

## Files

- `drug_qa_goldset.csv` — the gold set itself. 50 rows, 10 drugs × 5 categories.
- `run_eval.py` — runs every gold-set question against the production `/ask` endpoint and scores the response with Claude as a judge.
- `check_regression.py` — compares the latest run's hallucination rate against the previous run; non-zero exit if it regressed.
- `results_<UTC_timestamp>.csv` — output of each eval run. Append-only; never overwrite.

## What is the goldset?

A 50-question benchmark of patient-style medication questions with FDA-label-grounded expected answers. Every row covers one of five categories — `dose`, `side_effect`, `interaction`, `contraindication`, `mechanism` — and points at the FDA citation source the answer should rely on (`DailyMed`, `openFDA Label`, `FDA Drug Label`, or `RxNav` for interactions).

The 10 drugs covered are the most-prescribed outpatient medications in the US: `metformin`, `lisinopril`, `atorvastatin`, `levothyroxine`, `amlodipine`, `metoprolol`, `omeprazole`, `sertraline`, `ibuprofen`, `amoxicillin`.

## CSV columns

| Column | Description |
|---|---|
| `id` | Stable string ID. Format: `<3-letter drug prefix>-<3-digit serial>`. Once written, never re-used. |
| `drug` | Lowercase generic name only (e.g. `metformin`, never `Metformin HCl` or `Glucophage`). |
| `question` | Patient-phrased question. Mix of formal and casual. At least 2 of every 5 questions for a drug must be casual. |
| `expected_answer_keywords` | Pipe-separated list of 3–6 lowercase keywords/phrases. A correct answer **must** contain these. Specific enough that hallucinations miss them. |
| `expected_citation_source` | One of: `DailyMed`, `openFDA Label`, `FDA Drug Label`, `RxNav` (interactions only). |
| `category` | Exactly one of: `dose`, `side_effect`, `interaction`, `contraindication`, `mechanism`. |

## Adding new rows

The goldset is FDA-grounded. **Every new row must clear this bar before being merged:**

1. The expected answer must be verifiable on the FDA-approved label for that drug (DailyMed or Drugs@FDA).
2. The keywords must be specific — generic words like `medication` or `doctor` don't belong here.
3. The question must be answerable from the label alone. Patient-specific judgment calls (e.g. "Is X safe for me?") have no place in the goldset; reword as a label-verifiable fact ("Is X contraindicated in pregnancy?").
4. Cover all 5 categories per drug. Never add a sixth question to a drug without rebalancing.

Borderline rows get replaced, not relaxed.

## Running the eval locally

```bash
# from the repo root
make eval
```

Requires `ANTHROPIC_API_KEY` and `RXBUDDY_API_URL` in your `.env`. The runner emits one summary line and writes a timestamped results CSV.

## Nightly CI

GitHub Actions runs `make eval` every night at 06:00 UTC and again on demand. See `.github/workflows/eval.yml` and the *Nightly CI* section that the Phase 1.5 prompt adds to this file.
