"""
RxBuddy Services — MedlinePlus API Client
==========================================

Consumer-friendly drug information from NLM MedlinePlus.
No API key required. Free public API.

Functions
---------
get_health_topic(drug_name)    → consumer-friendly info dict
get_medlineplus_connect(rxcui) → medication guide dict by RxCUI

Both functions:
  - Return empty dict on any failure (never raise)
  - Enforce a 5-second timeout on every network call
  - Strip HTML tags from all text fields
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

logger = logging.getLogger("rxbuddy.services.medlineplus")

_CONNECT_URL = "https://connect.medlineplus.gov/application"
_HEALTH_URL = "https://wsearch.nlm.nih.gov/ws/query"
_TIMEOUT = 5  # seconds


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    clean = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", clean).strip()


def get_health_topic(drug_name: str) -> dict[str, str]:
    """
    Return consumer-friendly drug information from the MedlinePlus Health Topics API.

    Queries the MedlinePlus web service at wsearch.nlm.nih.gov.
    Returns a dict with keys:
        title      — drug/topic name
        summary    — plain-English summary (up to 400 chars)
        full_url   — link to the MedlinePlus page
    Returns {} on failure or no match.

    Example
    -------
    get_health_topic("metformin") → {"title": "Metformin", "summary": "...", "full_url": "..."}
    """
    if not drug_name or not drug_name.strip():
        return {}

    try:
        resp = requests.get(
            _HEALTH_URL,
            params={
                "db": "healthTopics",
                "term": drug_name.strip(),
                "retmax": "1",
                "rettype": "brief",
            },
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()

        # Response is XML; parse minimally for the key fields
        content = resp.text

        # Extract title from <content name="title">...</content>
        title_match = re.search(r'<content name="title"[^>]*>(.*?)</content>', content, re.DOTALL)
        title = _strip_html(title_match.group(1)) if title_match else ""

        # Extract snippet/summary
        snippet_match = re.search(r'<content name="snippet"[^>]*>(.*?)</content>', content, re.DOTALL)
        summary = _strip_html(snippet_match.group(1))[:400] if snippet_match else ""

        # Extract URL
        url_match = re.search(r'<url[^>]*>(.*?)</url>', content, re.DOTALL)
        full_url = _strip_html(url_match.group(1)) if url_match else ""

        if not summary and not title:
            return {}

        logger.debug("[MedlinePlus/topic] Found topic for '%s': %s", drug_name, title)
        return {
            "title":    title,
            "summary":  summary,
            "full_url": full_url,
        }
    except Exception as exc:
        logger.warning("[MedlinePlus/topic] get_health_topic('%s') failed: %s", drug_name, exc)
        return {}


def get_medlineplus_connect(rxcui: str) -> dict[str, str]:
    """
    Return medication guide information from MedlinePlus Connect by RxCUI.

    Uses the MedlinePlus Connect API (connect.medlineplus.gov).
    Returns a dict with keys:
        summary      — plain-English summary (up to 300 chars)
        usage        — usage / indication text (up to 300 chars)
        side_effects — side-effect description (up to 250 chars)
        title        — drug title from MedlinePlus
    Returns {} on failure, no match, or if rxcui is empty.

    Example
    -------
    get_medlineplus_connect("41493") → {"summary": "...", "usage": "...", ...}
    """
    if not rxcui or not rxcui.strip():
        return {}

    try:
        resp = requests.get(
            _CONNECT_URL,
            params={
                "mainSearchCriteria.v.cs": "2.16.840.1.113883.6.88",
                "mainSearchCriteria.v.c":  rxcui.strip(),
                "knowledgeResponseType":    "application/json",
            },
            headers={"User-Agent": "RxBuddy/1.0"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        data = resp.json()

        feed = data.get("feed", {})
        entries = feed.get("entry", [])
        if not entries:
            return {}

        entry = entries[0]
        title = entry.get("title", {}).get("_value", "")
        raw_summary = entry.get("summary", {}).get("_value", "")
        raw_content = entry.get("content", {}).get("_value", "")

        summary = _strip_html(raw_summary)[:300]
        full_text = _strip_html(raw_content)

        # Extract a usage snippet (look for "used to treat" or "is a medication")
        usage = ""
        usage_patterns = ["used to treat", "is a medication", "is used to", "treats", "indicated for"]
        lower_text = full_text.lower()
        for pat in usage_patterns:
            idx = lower_text.find(pat)
            if idx != -1:
                # Back up to start of sentence
                start = max(0, full_text.rfind(".", 0, idx) + 1)
                usage = full_text[start: idx + 250].strip()[:300]
                break

        # Extract side-effects sentence
        side_effects = ""
        se_idx = lower_text.find("side effect")
        if se_idx != -1:
            side_effects = full_text[se_idx: se_idx + 250]

        has_data = any([summary, usage, side_effects, title])
        if not has_data:
            return {}

        logger.debug("[MedlinePlus/connect] Found data for rxcui=%s (%s)", rxcui, title)
        return {
            "title":       title,
            "summary":     summary or (full_text[:300] if full_text else ""),
            "usage":       usage,
            "side_effects": side_effects,
        }
    except Exception as exc:
        logger.warning("[MedlinePlus/connect] get_medlineplus_connect('%s') failed: %s", rxcui, exc)
        return {}
