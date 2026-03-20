"""
RxBuddy Label Updater — v1
===========================

LABEL UPDATE PIPELINE
─────────────────────

  Trigger              │  Mechanism
  ─────────────────────┼──────────────────────────────────────────────────────
  On every query       │  get_cached_label() checks TTL; stale → re-fetch
  Daily cron (02:00Z)  │  refresh_stale_labels(fetcher) scans all cached drugs
  Manual admin         │  GET /admin/cache-stats, POST /admin/refresh-label
  Label revision Δ     │  effective_time field compared; stale if revision newer

METADATA TRACKED PER DRUG
──────────────────────────
  date_fetched         – UTC timestamp when we retrieved the label
  label_revision_date  – effective_time from FDA label (YYYYMMDD → ISO 8601)
  cache_expires_at     – date_fetched + CACHE_TTL_HOURS
  source               – "DailyMed" (via OpenFDA) or "Drugs@FDA"
  is_stale             – bool; True when now() > cache_expires_at

CACHE IMPLEMENTATION NOTES
───────────────────────────
  Current:  In-process dict (LRU eviction at MAX_CACHE_SIZE).
            Fast, zero-dependency, suitable for single-process deployments.
  Upgrade:  Replace with Redis for multi-process / Railway deployments.
            Use the same LabelCacheEntry structure; swap get/put to redis.get/set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger("rxbuddy.label_updater")

# ── Configuration ──────────────────────────────────────────────────────────────

CACHE_TTL_HOURS = 24    # Re-fetch labels older than this
MAX_CACHE_SIZE  = 500   # LRU eviction when cache exceeds this count


# ── Utilities ──────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Cache Entry ────────────────────────────────────────────────────────────────

class LabelCacheEntry:
    """
    Metadata wrapper for a cached FDA label.

    Attributes
    ----------
    data                : dict      – parsed label sections (fda_data dict)
    raw_label           : dict|None – raw OpenFDA label result (for metadata extraction)
    date_fetched        : datetime  – UTC timestamp of the fetch
    cache_expires_at    : datetime  – TTL expiry
    label_revision_date : str|None  – ISO 8601 date from effective_time field
    source              : str       – "DailyMed" or "Drugs@FDA"
    """
    __slots__ = (
        "data", "raw_label", "date_fetched",
        "cache_expires_at", "label_revision_date", "source",
    )

    def __init__(
        self,
        data: dict,
        raw_label: Optional[dict],
        date_fetched: datetime,
        label_revision_date: Optional[str] = None,
        source: str = "DailyMed",
    ) -> None:
        self.data                = data
        self.raw_label           = raw_label
        self.date_fetched        = date_fetched
        self.cache_expires_at    = date_fetched + timedelta(hours=CACHE_TTL_HOURS)
        self.label_revision_date = label_revision_date
        self.source              = source

    def is_stale(self) -> bool:
        return _utc_now() > self.cache_expires_at

    def metadata(self) -> dict:
        return {
            "date_fetched":        _iso(self.date_fetched),
            "cache_expires_at":    _iso(self.cache_expires_at),
            "label_revision_date": self.label_revision_date,
            "is_stale":            self.is_stale(),
            "source":              self.source,
        }


# ── In-Process Cache ───────────────────────────────────────────────────────────
# dict[drug_name_lower → LabelCacheEntry]
# Upgrade to Redis for multi-process / horizontal scaling.

_cache: dict[str, LabelCacheEntry] = {}


def get_cached_label(drug_name: str) -> Optional[LabelCacheEntry]:
    """
    Return a fresh (non-stale) cache entry, or None.

    None means: caller should re-fetch from OpenFDA and call put_label().
    """
    key = drug_name.strip().lower()
    entry = _cache.get(key)
    if entry is None:
        return None
    if entry.is_stale():
        logger.debug("[LabelCache] STALE for '%s' — will re-fetch", key)
        return None
    logger.debug("[LabelCache] HIT (fresh) for '%s'", key)
    return entry


def put_label(
    drug_name: str,
    data: dict,
    raw_label: Optional[dict],
    label_revision_date: Optional[str],
    source: str = "DailyMed",
) -> LabelCacheEntry:
    """
    Store or update a label in the cache.
    Applies simple LRU eviction when cache is full.
    """
    key = drug_name.strip().lower()

    # LRU eviction: remove the oldest entry when at capacity
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        oldest = min(_cache, key=lambda k: _cache[k].date_fetched)
        del _cache[oldest]
        logger.debug("[LabelCache] Evicted oldest entry: '%s'", oldest)

    entry = LabelCacheEntry(
        data=data,
        raw_label=raw_label,
        date_fetched=_utc_now(),
        label_revision_date=label_revision_date,
        source=source,
    )
    _cache[key] = entry
    logger.info(
        "[LabelCache] Cached label for '%s' | revision: %s | expires: %s",
        key,
        label_revision_date or "unknown",
        _iso(entry.cache_expires_at),
    )
    return entry


def get_label_metadata(drug_name: str) -> Optional[dict]:
    """Return metadata dict for a cached drug, or None if not in cache."""
    entry = _cache.get(drug_name.strip().lower())
    return entry.metadata() if entry else None


# ── Refresh Pipeline ───────────────────────────────────────────────────────────

FetcherFn = Callable[[str], "tuple[dict | None, dict | None]"]
"""
Fetcher callable signature:
  fetcher(drug_name: str) → (parsed_fda_data: dict | None, raw_openfda_result: dict | None)

This matches the return signature of `_fetch_fda_label_with_raw()` in main.py.
"""


def refresh_stale_labels(fetcher: FetcherFn) -> list[str]:
    """
    Refresh all stale cache entries using the provided fetcher function.

    Intended to be called by a scheduled task (e.g., daily cron at 02:00 UTC).
    Also evicts drugs whose labels are no longer found in OpenFDA.

    Parameters
    ----------
    fetcher : callable
        Signature: (drug_name: str) → (fda_data: dict|None, raw_label: dict|None)

    Returns
    -------
    list[str]
        Names of drugs whose labels were successfully refreshed.

    Example cron integration (APScheduler)
    ---------------------------------------
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: refresh_stale_labels(my_fetcher),
        trigger="cron", hour=2, minute=0, timezone="UTC",
    )
    scheduler.start()
    """
    stale_keys = [k for k, v in _cache.items() if v.is_stale()]
    logger.info("[LabelUpdater] Refreshing %d stale labels: %s", len(stale_keys), stale_keys)

    refreshed: list[str] = []
    for drug_name in stale_keys:
        try:
            data, raw = fetcher(drug_name)
            if data:
                # Extract revision date from raw label
                revision: Optional[str] = None
                if raw:
                    try:
                        from answer_engine import extract_fda_metadata
                        revision = extract_fda_metadata(raw).get("label_revision_date")
                    except Exception:
                        pass
                put_label(drug_name, data, raw, revision)
                refreshed.append(drug_name)
                logger.info("[LabelUpdater] Refreshed '%s' (revision: %s)", drug_name, revision)
            else:
                # Label no longer available — remove from cache
                del _cache[drug_name]
                logger.info("[LabelUpdater] Label gone for '%s' — evicted from cache", drug_name)
        except Exception as exc:
            logger.warning("[LabelUpdater] Failed to refresh '%s': %s", drug_name, exc)

    return refreshed


def cache_stats() -> dict:
    """
    Return cache statistics for health monitoring.

    Example response
    ----------------
    {
      "total_cached": 42,
      "fresh_count": 39,
      "stale_count": 3,
      "stale_entries": ["warfarin", "lisinopril", "atorvastatin"],
      "cache_ttl_hours": 24,
      "max_cache_size": 500,
      "checked_at": "2026-03-19T10:00:00Z"
    }
    """
    stale  = [k for k, v in _cache.items() if v.is_stale()]
    fresh  = [k for k in _cache if k not in stale]
    return {
        "total_cached":   len(_cache),
        "fresh_count":    len(fresh),
        "stale_count":    len(stale),
        "stale_entries":  stale,
        "cache_ttl_hours": CACHE_TTL_HOURS,
        "max_cache_size":  MAX_CACHE_SIZE,
        "checked_at":      _iso(_utc_now()),
    }
