"""Regression alarm for the nightly eval.

Compares the hallucination rate of a new results CSV against the
immediately preceding results CSV in the same directory.

Exit codes:
    0 — no regression (or first run, or new ≤ prev + threshold)
    1 — hallucination rate rose by more than the threshold (default 1.0pp)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_TIMESTAMP_RE = re.compile(r"results_(\d{8}T\d{6}Z)\.csv$")


def _parse_ts(path: Path) -> datetime | None:
    m = _TIMESTAMP_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hallucination_rate(csv_path: Path) -> float | None:
    scored = 0
    hallucinated = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not (row.get("keyword_coverage") or "").strip():
                continue
            scored += 1
            if (row.get("hallucination_present") or "").strip().lower() in {"true", "1"}:
                hallucinated += 1
    if scored == 0:
        return None
    return hallucinated / scored * 100.0


def _find_previous(new_path: Path) -> Path | None:
    """Among all results_*.csv in the same dir, find the one immediately before new_path."""
    new_ts = _parse_ts(new_path)
    if new_ts is None:
        # Fall back to mtime if filename can't be parsed.
        new_ts = datetime.fromtimestamp(new_path.stat().st_mtime, tz=timezone.utc)

    candidates: list[tuple[datetime, Path]] = []
    for p in new_path.parent.glob("results_*.csv"):
        if p.resolve() == new_path.resolve():
            continue
        ts = _parse_ts(p) or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if ts < new_ts:
            candidates.append((ts, p))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("new_csv", help="Path to the just-generated results CSV.")
    ap.add_argument(
        "--threshold-pp",
        type=float,
        default=1.0,
        help="Allowable increase in hallucination rate (percentage points).",
    )
    args = ap.parse_args()

    new_path = Path(args.new_csv)
    if not new_path.exists():
        print(f"ERROR: {new_path} does not exist", file=sys.stderr)
        return 1

    new_rate = _hallucination_rate(new_path)
    if new_rate is None:
        print(f"WARNING: {new_path} has zero scored rows; skipping regression check", file=sys.stderr)
        return 0

    prev_path = _find_previous(new_path)
    if prev_path is None:
        print(f"OK: no prior results CSV - first run. new={new_rate:.1f}%")
        return 0

    prev_rate = _hallucination_rate(prev_path)
    if prev_rate is None:
        print(
            f"OK: prior results CSV {prev_path.name} has no scored rows; skipping",
            file=sys.stderr,
        )
        return 0

    delta = new_rate - prev_rate
    if delta > args.threshold_pp:
        print(
            f"REGRESSION: hallucination rate rose from {prev_rate:.1f}% to {new_rate:.1f}% "
            f"(delta +{delta:.1f} pp). Failing CI.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: hallucination {prev_rate:.1f}% -> {new_rate:.1f}% "
        f"(delta {delta:+.1f} pp, threshold {args.threshold_pp:.1f} pp)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
