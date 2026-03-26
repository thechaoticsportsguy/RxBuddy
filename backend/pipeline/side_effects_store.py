"""
Side Effects Store — PostgreSQL-backed persistent drug side effect data.

Architecture:
  - Two new tables: `drugs` (canonical drug info) + `drug_side_effects` (structured effects)
  - Gemini parses FDA label text into structured rows on first lookup
  - Subsequent lookups hit the DB (fast) instead of re-fetching + re-parsing
  - Falls back gracefully if DB is unavailable

Usage:
  from pipeline.side_effects_store import get_or_fetch_side_effects
  data = await get_or_fetch_side_effects("metformin", fda_label, raw_label)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rxbuddy.pipeline.se_store")

# ---------------------------------------------------------------------------
# DB setup — reuse the same DATABASE_URL as main.py
# ---------------------------------------------------------------------------

def _database_url() -> str | None:
    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def _get_engine():
    """Return a SQLAlchemy engine, or None if DATABASE_URL is not set."""
    try:
        from sqlalchemy import create_engine
        url = _database_url()
        if not url:
            return None
        return create_engine(url, future=True, pool_pre_ping=True)
    except Exception as exc:
        logger.warning("[SEStore] Could not create engine: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Table creation — runs once at startup
# ---------------------------------------------------------------------------

_TABLES_CREATED = False

_CREATE_DRUGS_TABLE = """
CREATE TABLE IF NOT EXISTS drugs (
    id              SERIAL PRIMARY KEY,
    generic_name    VARCHAR(255) UNIQUE NOT NULL,
    brand_names     TEXT[],
    ndc_code        VARCHAR(20),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

_CREATE_SE_TABLE = """
CREATE TABLE IF NOT EXISTS drug_side_effects (
    id                  SERIAL PRIMARY KEY,
    drug_generic_name   VARCHAR(255) NOT NULL,
    display_name        VARCHAR(500) NOT NULL,
    frequency           VARCHAR(20),   -- 'very_common' | 'common' | 'uncommon' | 'rare' | 'serious'
    frequency_percent   FLOAT,
    severity            VARCHAR(20),   -- 'mild' | 'moderate' | 'severe'
    patient_description TEXT,
    management          VARCHAR(100),  -- 'monitor' | 'manage_at_home' | 'contact_doctor'
    red_flag            BOOLEAN DEFAULT FALSE,
    evidence_tier       VARCHAR(20),   -- 'label' | 'faers'
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(drug_generic_name, display_name)
);
"""

_CREATE_SE_META_TABLE = """
CREATE TABLE IF NOT EXISTS drug_se_meta (
    drug_generic_name   VARCHAR(255) PRIMARY KEY,
    brand_names         TEXT[],
    generic_name_label  VARCHAR(255),
    boxed_warnings      TEXT[],
    moa_summary         TEXT,
    moa_detail          TEXT,
    pharmacologic_class VARCHAR(500),
    molecular_targets   TEXT[],
    dailymed_url        TEXT,
    fda_url             TEXT,
    label_date          VARCHAR(20),
    parsed_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""


def ensure_tables() -> bool:
    """Create tables if they don't exist. Returns True on success."""
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return True
    engine = _get_engine()
    if not engine:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(__import__("sqlalchemy").text(_CREATE_DRUGS_TABLE))
            conn.execute(__import__("sqlalchemy").text(_CREATE_SE_TABLE))
            conn.execute(__import__("sqlalchemy").text(_CREATE_SE_META_TABLE))
        _TABLES_CREATED = True
        logger.info("[SEStore] Tables ensured")
        return True
    except Exception as exc:
        logger.warning("[SEStore] ensure_tables failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# DB read/write helpers
# ---------------------------------------------------------------------------

def get_from_db(drug_name: str) -> dict | None:
    """
    Look up structured side effects for a drug from the DB.
    Returns the full structured response dict (same shape as parse_structured_side_effects),
    or None if not found.
    """
    if not drug_name:
        return None
    ensure_tables()
    engine = _get_engine()
    if not engine:
        return None

    from sqlalchemy import text
    key = drug_name.strip().lower()
    try:
        with engine.connect() as conn:
            # Fetch meta row
            meta = conn.execute(
                text("SELECT * FROM drug_se_meta WHERE drug_generic_name = :name"),
                {"name": key},
            ).mappings().first()

            if not meta:
                return None

            # Fetch side effect rows
            rows = conn.execute(
                text("SELECT * FROM drug_side_effects WHERE drug_generic_name = :name"),
                {"name": key},
            ).mappings().all()

        # Build the same shape as parse_structured_side_effects()
        tiers: dict[str, Any] = {
            "very_common": {"label": "Very Common (>10%)", "items": []},
            "common":      {"label": "Common (1-10%)",    "items": []},
            "uncommon":    {"label": "Uncommon (<1%)",     "items": []},
            "serious":     {"label": "Serious — Seek Immediate Medical Attention",
                            "items": [], "urgent": True},
        }
        for row in rows:
            freq = row["frequency"] or "common"
            display = row["display_name"] or ""
            if freq in tiers:
                tiers[freq]["items"].append(display)

        sources = []
        if meta["dailymed_url"]:
            sources.append({
                "id": 1,
                "name": f"DailyMed — {key.title()} label",
                "url": meta["dailymed_url"],
                "section": "ADVERSE REACTIONS",
                "last_updated": meta["label_date"] or "",
            })
        if meta["fda_url"]:
            sources.append({
                "id": 2,
                "name": "openFDA Drug Label API",
                "url": meta["fda_url"],
                "section": "adverse_reactions",
                "last_updated": meta["label_date"] or "",
            })

        return {
            "drug_name":  key,
            "generic_name": meta["generic_name_label"] or key,
            "brand_names": list(meta["brand_names"] or []),
            "side_effects": tiers,
            "boxed_warnings": list(meta["boxed_warnings"] or []),
            "mechanism_of_action": {
                "summary": meta["moa_summary"] or "",
                "detail":  meta["moa_detail"] or "",
                "pharmacologic_class": meta["pharmacologic_class"] or "",
                "molecular_targets": list(meta["molecular_targets"] or []),
            },
            "sources": sources,
            "_from_db": True,
        }

    except Exception as exc:
        logger.warning("[SEStore] get_from_db(%s) failed: %s", drug_name, exc)
        return None


def store_to_db(drug_name: str, parsed: dict) -> bool:
    """
    Persist a parsed side-effects dict (from parse_label_with_gemini or
    parse_structured_side_effects) into the DB.
    Returns True on success.
    """
    if not drug_name or not parsed:
        return False
    ensure_tables()
    engine = _get_engine()
    if not engine:
        return False

    from sqlalchemy import text
    key = drug_name.strip().lower()

    # Extract fields
    brand_names = parsed.get("brand_names", [])
    generic_name_label = parsed.get("generic_name", key)
    boxed_warnings = parsed.get("boxed_warnings", [])
    moa = parsed.get("mechanism_of_action", {})
    sources = parsed.get("sources", [])
    dailymed_url = next((s["url"] for s in sources if "dailymed" in s.get("url", "").lower()), None)
    fda_url = next((s["url"] for s in sources if "api.fda.gov" in s.get("url", "").lower()), None)
    label_date = sources[0].get("last_updated", "") if sources else ""

    try:
        with engine.begin() as conn:
            # Upsert meta
            conn.execute(text("""
                INSERT INTO drug_se_meta
                    (drug_generic_name, brand_names, generic_name_label,
                     boxed_warnings, moa_summary, moa_detail,
                     pharmacologic_class, molecular_targets,
                     dailymed_url, fda_url, label_date, parsed_at)
                VALUES
                    (:name, :brands, :generic_label,
                     :boxed, :moa_sum, :moa_det,
                     :pharm_class, :targets,
                     :dm_url, :fda_url, :ldate, NOW())
                ON CONFLICT (drug_generic_name) DO UPDATE SET
                    brand_names         = EXCLUDED.brand_names,
                    generic_name_label  = EXCLUDED.generic_name_label,
                    boxed_warnings      = EXCLUDED.boxed_warnings,
                    moa_summary         = EXCLUDED.moa_summary,
                    moa_detail          = EXCLUDED.moa_detail,
                    pharmacologic_class = EXCLUDED.pharmacologic_class,
                    molecular_targets   = EXCLUDED.molecular_targets,
                    dailymed_url        = EXCLUDED.dailymed_url,
                    fda_url             = EXCLUDED.fda_url,
                    label_date          = EXCLUDED.label_date,
                    parsed_at           = NOW()
            """), {
                "name":          key,
                "brands":        brand_names,
                "generic_label": generic_name_label,
                "boxed":         boxed_warnings,
                "moa_sum":       moa.get("summary", ""),
                "moa_det":       moa.get("detail", ""),
                "pharm_class":   moa.get("pharmacologic_class", ""),
                "targets":       moa.get("molecular_targets", []),
                "dm_url":        dailymed_url,
                "fda_url":       fda_url,
                "ldate":         label_date,
            })

            # Upsert each side effect row
            side_effects = parsed.get("side_effects", {})
            for freq_key, tier in side_effects.items():
                for item in tier.get("items", []):
                    if not item:
                        continue
                    severity = "severe" if freq_key == "serious" else (
                        "moderate" if freq_key in ("uncommon",) else "mild"
                    )
                    conn.execute(text("""
                        INSERT INTO drug_side_effects
                            (drug_generic_name, display_name, frequency,
                             severity, evidence_tier, updated_at)
                        VALUES
                            (:name, :disp, :freq, :sev, 'label', NOW())
                        ON CONFLICT (drug_generic_name, display_name) DO UPDATE SET
                            frequency    = EXCLUDED.frequency,
                            severity     = EXCLUDED.severity,
                            updated_at   = NOW()
                    """), {
                        "name": key,
                        "disp": str(item)[:500],
                        "freq": freq_key,
                        "sev":  severity,
                    })

        logger.info("[SEStore] Stored %s side effects for %s", key,
                    sum(len(t.get("items", [])) for t in side_effects.values()))
        return True

    except Exception as exc:
        logger.warning("[SEStore] store_to_db(%s) failed: %s", drug_name, exc)
        return False


# ---------------------------------------------------------------------------
# Gemini-powered FDA label parser
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = """You are a clinical pharmacist extracting side effects from an FDA drug label.

Return ONLY valid JSON (no markdown, no extra text) in this exact structure:
{
  "very_common": ["effect1", "effect2"],
  "common": ["effect3", "effect4"],
  "uncommon": ["effect5"],
  "serious": ["serious effect 1", "serious effect 2"],
  "boxed_warnings": ["boxed warning text if present"],
  "moa_summary": "1-2 sentence patient-friendly mechanism of action",
  "moa_detail": "3-5 sentence clinical mechanism detail"
}

Classification rules:
- very_common: explicitly stated >10% or "most common" or "frequently"
- common: 1-10% or listed in adverse reactions without frequency qualifier
- uncommon: <1% or "rare" or "infrequent" or "uncommon"
- serious: from warnings/precautions/boxed_warning sections — life-threatening, severe, requires medical attention
- boxed_warnings: exact short phrases from any BLACK BOX WARNING section

STRICT rules:
- NEVER put death, overdose, addiction, cardiac arrest, respiratory failure, coma in the common or very_common lists
- Those belong ONLY in serious or boxed_warnings
- Keep each item short (2-6 words): "nausea", "diarrhea", "headache", "dry mouth"
- Maximum 8 items per tier
- If a section has no data, use an empty list []
"""


def parse_label_with_gemini(drug_name: str, fda_label: dict) -> dict | None:
    """
    Use Gemini to parse an FDA label dict into structured side effects.
    Returns a dict in the same shape as parse_structured_side_effects(),
    or None if Gemini is unavailable.
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        logger.warning("[SEStore] No GEMINI_API_KEY — skipping Gemini parse")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
    except Exception as exc:
        logger.warning("[SEStore] Gemini import/configure failed: %s", exc)
        return None

    # Build the label context — only include the relevant sections
    sections = []
    for section in ("adverse_reactions", "warnings", "boxed_warning",
                    "warnings_and_precautions", "clinical_pharmacology", "description"):
        text = (fda_label or {}).get(section, "")
        if text:
            sections.append(f"=== {section.upper()} ===\n{text[:1000]}")

    if not sections:
        logger.warning("[SEStore] No FDA label sections for %s — skipping parse", drug_name)
        return None

    label_text = "\n\n".join(sections)
    user_msg = f"Drug: {drug_name}\n\n{label_text}"

    try:
        resp = model.generate_content(
            [{"role": "user", "parts": [_PARSE_SYSTEM + "\n\n" + user_msg]}],
            generation_config={"temperature": 0.1, "max_output_tokens": 800},
        )
        raw = (resp.text or "").strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("[SEStore] Gemini parse failed for %s: %s", drug_name, exc)
        return None

    # Build into the standard shape
    def _items(key: str) -> list[str]:
        val = parsed.get(key, [])
        return [str(v).strip() for v in val if v][:8]

    return {
        "drug_name": drug_name,
        "generic_name": drug_name,
        "brand_names": [],
        "side_effects": {
            "very_common": {"label": "Very Common (>10%)",  "items": _items("very_common")},
            "common":      {"label": "Common (1-10%)",      "items": _items("common")},
            "uncommon":    {"label": "Uncommon (<1%)",       "items": _items("uncommon")},
            "serious":     {"label": "Serious — Seek Immediate Medical Attention",
                            "items": _items("serious"), "urgent": True},
        },
        "boxed_warnings": _items("boxed_warnings"),
        "mechanism_of_action": {
            "summary":             parsed.get("moa_summary", "")[:300],
            "detail":              parsed.get("moa_detail", "")[:800],
            "pharmacologic_class": "",
            "molecular_targets":   [],
        },
        "sources": [],
        "_from_gemini": True,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_or_fetch_side_effects(
    drug_name: str,
    fda_label: dict | None,
    raw_label: dict | None = None,
    dailymed_setid: str | None = None,
) -> dict | None:
    """
    Return structured side effects for a drug.

    Priority:
      1. DB cache (fast, already parsed)
      2. Gemini parse of FDA label + store to DB
      3. None (caller falls back to parse_structured_side_effects)
    """
    if not drug_name:
        return None

    # 1. DB lookup
    cached = get_from_db(drug_name)
    if cached:
        logger.info("[SEStore] Cache hit for %s", drug_name)
        return cached

    if not fda_label:
        return None

    # 2. Gemini parse
    logger.info("[SEStore] Cache miss for %s — parsing with Gemini", drug_name)
    parsed = parse_label_with_gemini(drug_name, fda_label)
    if not parsed:
        return None

    # Enrich with metadata from raw openFDA result
    if raw_label:
        openfda = raw_label.get("openfda", {})
        set_id = (openfda.get("set_id") or [None])[0]
        nda = (openfda.get("application_number") or [None])[0]
        eff_date = raw_label.get("effective_time", "")
        label_date = ""
        if eff_date and len(eff_date) >= 8:
            label_date = f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}"

        parsed["brand_names"] = list(openfda.get("brand_name", []))[:5]
        parsed["generic_name"] = (openfda.get("generic_name") or [drug_name])[0]
        parsed["mechanism_of_action"]["pharmacologic_class"] = (
            (openfda.get("pharm_class_epc") or openfda.get("pharm_class_moa") or [""])[0]
        )

        effective_setid = dailymed_setid or set_id
        sources = []
        if effective_setid:
            sources.append({
                "id": 1,
                "name": f"DailyMed — {drug_name.title()} label",
                "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={effective_setid}",
                "section": "ADVERSE REACTIONS",
                "last_updated": label_date,
            })
        sources.append({
            "id": len(sources) + 1,
            "name": "openFDA Drug Label API",
            "url": f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{drug_name}"&limit=1',
            "section": "adverse_reactions",
            "last_updated": label_date,
        })
        parsed["sources"] = sources

    # 3. Store to DB (non-blocking — don't let a DB failure crash the request)
    try:
        store_to_db(drug_name, parsed)
    except Exception as exc:
        logger.warning("[SEStore] Background store failed for %s: %s", drug_name, exc)

    return parsed
