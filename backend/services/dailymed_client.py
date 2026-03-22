"""
RxBuddy Services — DailyMed SPL Client
========================================

get_dailymed_set_id(drug_name) → {setid, title, link}

Hits the DailyMed v2 SPLs endpoint and returns the first matching label.
Falls back gracefully on network errors or no match.

The parent module backend/rxnorm_client.py already has get_dailymed_setid()
(which returns just the setID string).  This module returns a richer dict as
required by the normalization pipeline spec.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from typing import Optional

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import rxnorm_client as _parent  # noqa: E402

logger = logging.getLogger("rxbuddy.services.dailymed")

_DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
_DAILYMED_VIEW_URL = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"


@lru_cache(maxsize=1024)
def get_dailymed_set_id(drug_name: str) -> dict:
    """
    Look up the DailyMed SPL setID for a drug name.

    Returns a dict:
        setid   — DailyMed SPL setID UUID (empty string if not found)
        title   — label title / drug name from DailyMed
        link    — direct URL to the DailyMed drug label page

    Example
    -------
    get_dailymed_set_id("warfarin")
    → {
        "setid": "8d24adac-fda8-...",
        "title": "WARFARIN SODIUM tablet",
        "link": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=8d24adac-..."
      }
    """
    if not drug_name or not drug_name.strip():
        return {"setid": "", "title": "", "link": ""}

    name = drug_name.strip()

    try:
        data = _parent._get(
            f"{_DAILYMED_BASE}/spls.json",
            {"drug_name": name, "pagesize": 1},
        )
        items = data.get("data") or []
        if not items:
            logger.debug("[DailyMed] No SPL found for '%s'", name)
            return {"setid": "", "title": "", "link": ""}

        item = items[0]
        setid = str(item.get("setid", "") or "")
        title = str(item.get("title", "") or "")
        link = _DAILYMED_VIEW_URL.format(setid=setid) if setid else ""

        logger.debug("[DailyMed] SPL for '%s': setid=%s", name, setid)
        return {"setid": setid, "title": title, "link": link}

    except Exception as exc:
        logger.warning("[DailyMed] get_dailymed_set_id failed for '%s': %s", name, exc)
        return {"setid": "", "title": "", "link": ""}


def attach_dailymed(resolved: dict) -> dict:
    """
    Enrich a drug_resolver result dict with DailyMed setID data.

    Tries the generic name first; falls back to the first brand name.
    Modifies and returns the dict in-place.
    """
    generic = resolved.get("generic", "")
    brands = resolved.get("brands", [])

    dm = get_dailymed_set_id(generic)
    if not dm["setid"] and brands:
        dm = get_dailymed_set_id(brands[0])

    resolved["dailymed_setid"] = dm["setid"]
    resolved["dailymed_title"] = dm["title"]
    resolved["dailymed_link"] = dm["link"]
    return resolved
