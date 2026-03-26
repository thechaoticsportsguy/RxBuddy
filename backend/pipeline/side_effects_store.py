"""
Side Effects Store — PostgreSQL-backed persistent drug side effect data.

Architecture:
  - drug_se_meta table: per-drug metadata (brand names, MOA, sources)
  - drug_side_effects table: individual side effect rows with rich fields
  - Gemini parses FDA label text into structured rows on first lookup
  - Subsequent lookups hit the DB (< 10ms) instead of re-fetching + re-parsing
  - Falls back gracefully if DB or Gemini is unavailable

Data extracted per side effect:
  display_name, frequency (very_common/common/uncommon/serious),
  frequency_percent, severity, patient_description, onset_days,
  resolution_days, management, red_flag, red_flag_reason, evidence_tier
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("rxbuddy.pipeline.se_store")

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def _database_url() -> str | None:
    return os.getenv("DATABASE_URL", "").strip() or None


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
# Table creation — runs once per process
# ---------------------------------------------------------------------------

_TABLES_CREATED = False

# drug_se_meta: one row per drug (metadata + MOA)
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

# drug_side_effects: one row per (drug, side_effect) with rich clinical fields
_CREATE_SE_TABLE = """
CREATE TABLE IF NOT EXISTS drug_side_effects (
    id                  SERIAL PRIMARY KEY,
    drug_generic_name   VARCHAR(255) NOT NULL,
    display_name        VARCHAR(500) NOT NULL,
    frequency           VARCHAR(20),
    frequency_percent   FLOAT,
    severity            VARCHAR(20),
    patient_description TEXT,
    onset_days          INT,
    resolution_days     INT,
    management          VARCHAR(100),
    red_flag            BOOLEAN DEFAULT FALSE,
    red_flag_reason     TEXT,
    evidence_tier       VARCHAR(20) DEFAULT 'label',
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(drug_generic_name, display_name)
);
"""

# ALTER statements to add columns that may be missing on older deployments
_MIGRATE_SE_TABLE = [
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS onset_days INT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS resolution_days INT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS patient_description TEXT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS management VARCHAR(100);",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS red_flag BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS red_flag_reason TEXT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS frequency_percent FLOAT;",
]


def ensure_tables() -> bool:
    """Create / migrate tables. Returns True on success."""
    global _TABLES_CREATED
    if _TABLES_CREATED:
        return True
    engine = _get_engine()
    if not engine:
        return False
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(_CREATE_SE_META_TABLE))
            conn.execute(text(_CREATE_SE_TABLE))
            for stmt in _MIGRATE_SE_TABLE:
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass  # column may already exist on a fresh table
        _TABLES_CREATED = True
        logger.info("[SEStore] Tables ready")
        return True
    except Exception as exc:
        logger.warning("[SEStore] ensure_tables failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# DB read helpers
# ---------------------------------------------------------------------------

def get_from_db(drug_name: str) -> dict | None:
    """
    Return structured side effects from the DB cache, or None if not found.
    Result shape is compatible with what AnswerCard.js expects.
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
            meta = conn.execute(
                text("SELECT * FROM drug_se_meta WHERE drug_generic_name = :name"),
                {"name": key},
            ).mappings().first()
            if not meta:
                return None

            rows = conn.execute(
                text("SELECT * FROM drug_side_effects WHERE drug_generic_name = :name ORDER BY id"),
                {"name": key},
            ).mappings().all()

        # Build tiers — items are rich dicts now (display_name + extra fields)
        tiers: dict[str, Any] = {
            "very_common": {"label": "Very Common (>10%)",  "items": []},
            "common":      {"label": "Common (1-10%)",       "items": []},
            "uncommon":    {"label": "Uncommon (<1%)",        "items": []},
            "serious":     {"label": "Serious — Seek Immediate Medical Attention",
                            "items": [], "urgent": True},
        }
        for row in rows:
            freq = row["frequency"] or "common"
            if freq not in tiers:
                freq = "common"
            # Build a rich item dict; AnswerCard only needs the string name
            # but the new API endpoint returns the full object
            rich = {
                "display_name":       row["display_name"] or "",
                "frequency":          freq,
                "frequency_percent":  row["frequency_percent"],
                "severity":           row["severity"] or "mild",
                "patient_description": row["patient_description"] or "",
                "onset_days":         row["onset_days"],
                "resolution_days":    row["resolution_days"],
                "management":         row["management"] or "monitor",
                "red_flag":           bool(row["red_flag"]),
                "red_flag_reason":    row["red_flag_reason"] or "",
                "evidence_tier":      row["evidence_tier"] or "label",
                "data_source":        "FDA Label (DailyMed)",
            }
            tiers[freq]["items"].append(rich)

        # Build sources list
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
            "drug_name":   key,
            "generic_name": meta["generic_name_label"] or key,
            "brand_names":  list(meta["brand_names"] or []),
            "side_effects": tiers,
            "boxed_warnings": list(meta["boxed_warnings"] or []),
            "mechanism_of_action": {
                "summary":             meta["moa_summary"] or "",
                "detail":              meta["moa_detail"] or "",
                "pharmacologic_class": meta["pharmacologic_class"] or "",
                "molecular_targets":   list(meta["molecular_targets"] or []),
            },
            "sources":    sources,
            "_from_db":   True,
        }

    except Exception as exc:
        logger.warning("[SEStore] get_from_db(%s) failed: %s", drug_name, exc)
        return None


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def store_to_db(drug_name: str, parsed: dict) -> bool:
    """
    Upsert parsed side effects into DB. Handles both the new rich-object format
    (items are dicts) and the old plain-string format (items are strings).
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

    brand_names = parsed.get("brand_names", [])
    generic_name_label = parsed.get("generic_name", key)
    boxed_warnings = parsed.get("boxed_warnings", [])
    moa = parsed.get("mechanism_of_action", {})
    sources = parsed.get("sources", [])
    dailymed_url = next(
        (s["url"] for s in sources if "dailymed" in s.get("url", "").lower()), None
    )
    fda_url = next(
        (s["url"] for s in sources if "api.fda.gov" in s.get("url", "").lower()), None
    )
    label_date = sources[0].get("last_updated", "") if sources else ""

    try:
        with engine.begin() as conn:
            # Upsert meta row
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
                "name": key, "brands": brand_names,
                "generic_label": generic_name_label, "boxed": boxed_warnings,
                "moa_sum": moa.get("summary", ""), "moa_det": moa.get("detail", ""),
                "pharm_class": moa.get("pharmacologic_class", ""),
                "targets": moa.get("molecular_targets", []),
                "dm_url": dailymed_url, "fda_url": fda_url, "ldate": label_date,
            })

            # Upsert each side effect row
            side_effects = parsed.get("side_effects", {})
            count = 0
            for freq_key, tier in side_effects.items():
                for item in tier.get("items", []):
                    if not item:
                        continue

                    # Item may be a rich dict (new format) or a plain string (old format)
                    if isinstance(item, dict):
                        display  = str(item.get("display_name", "")).strip()[:500]
                        freq_pct = item.get("frequency_percent")
                        severity = item.get("severity") or (
                            "severe" if freq_key == "serious" else "mild"
                        )
                        desc     = str(item.get("patient_description", ""))[:1000]
                        onset    = item.get("onset_days")
                        res      = item.get("resolution_days")
                        mgmt     = str(item.get("management", "monitor"))[:100]
                        red_flag = bool(item.get("red_flag", False))
                        red_why  = str(item.get("red_flag_reason", ""))[:500] or None
                    else:
                        display  = str(item).strip()[:500]
                        freq_pct = None
                        severity = "severe" if freq_key == "serious" else "mild"
                        desc     = ""
                        onset    = None
                        res      = None
                        mgmt     = "contact_doctor" if freq_key == "serious" else "monitor"
                        red_flag = freq_key == "serious"
                        red_why  = None

                    if not display:
                        continue

                    conn.execute(text("""
                        INSERT INTO drug_side_effects
                            (drug_generic_name, display_name, frequency,
                             frequency_percent, severity, patient_description,
                             onset_days, resolution_days, management,
                             red_flag, red_flag_reason, evidence_tier, updated_at)
                        VALUES
                            (:name, :disp, :freq,
                             :fpct, :sev, :desc,
                             :onset, :res, :mgmt,
                             :rflag, :rwhy, 'label', NOW())
                        ON CONFLICT (drug_generic_name, display_name) DO UPDATE SET
                            frequency         = EXCLUDED.frequency,
                            frequency_percent = EXCLUDED.frequency_percent,
                            severity          = EXCLUDED.severity,
                            patient_description = EXCLUDED.patient_description,
                            onset_days        = EXCLUDED.onset_days,
                            resolution_days   = EXCLUDED.resolution_days,
                            management        = EXCLUDED.management,
                            red_flag          = EXCLUDED.red_flag,
                            red_flag_reason   = EXCLUDED.red_flag_reason,
                            updated_at        = NOW()
                    """), {
                        "name": key, "disp": display, "freq": freq_key,
                        "fpct": freq_pct, "sev": severity, "desc": desc,
                        "onset": onset, "res": res, "mgmt": mgmt,
                        "rflag": red_flag, "rwhy": red_why,
                    })
                    count += 1

        logger.info("[SEStore] Stored %d side effects for %s", count, key)
        return True

    except Exception as exc:
        logger.warning("[SEStore] store_to_db(%s) failed: %s", drug_name, exc)
        return False


# ---------------------------------------------------------------------------
# Gemini-powered FDA label parser (upgraded prompt)
# ---------------------------------------------------------------------------

def _build_gemini_prompt(drug_name: str, label_text: str) -> str:
    return f"""You are a pharmaceutical NLP expert parsing FDA drug labels.

Extract ALL side effects from this label for "{drug_name}".

For EACH side effect return a JSON object with these fields:
1. display_name        — patient-friendly name (e.g. "Diarrhea", not "Bowel disorder")
2. frequency           — "very_common" (>25%), "common" (10-25%), "uncommon" (1-10%), "serious" (<1% but important)
3. frequency_percent   — exact number if stated (e.g. "30% of patients" → 30), or null
4. severity            — "mild" (goes away on own), "moderate" (needs management), "severe" (seek help immediately)
5. patient_description — 1-2 plain-English sentences: what it feels like
6. onset_days          — days after starting when it typically appears ("within 3 days" → 3), or null
7. resolution_days     — days until it goes away ("usually within 1 week" → 7), or null
8. management          — "monitor", "manage_at_home", or "contact_doctor"
9. red_flag            — true only if life-threatening or requires immediate medical attention
10. red_flag_reason    — why it is urgent (null if red_flag=false)

CRITICAL RULES:
- Do NOT invent side effects not in the label
- NEVER put death, overdose, addiction, cardiac arrest, respiratory failure in common or very_common
  (those belong in serious only)
- If a percentage is mentioned, always include it in frequency_percent
- If timing is vague or not stated, use null
- Be conservative with red_flag (only for truly life-threatening cases)

Return ONLY a valid JSON array — no markdown, no explanations:

[
  {{
    "display_name": "Diarrhea",
    "frequency": "very_common",
    "frequency_percent": 30,
    "severity": "mild",
    "patient_description": "Loose stools, usually in the first 2 weeks. Often improves with diet adjustments.",
    "onset_days": 1,
    "resolution_days": 7,
    "management": "manage_at_home",
    "red_flag": false,
    "red_flag_reason": null
  }}
]

FDA LABEL TEXT FOR {drug_name.upper()}:
{label_text}"""


def parse_label_with_gemini(drug_name: str, fda_label: dict) -> dict | None:
    """
    Use Gemini to parse an FDA label dict into rich structured side effects.
    Returns a dict in the standard shape, or None if Gemini is unavailable.
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

    # Build label context — only sections relevant to side effects
    section_texts = []
    for section in ("adverse_reactions", "warnings", "boxed_warning",
                    "warnings_and_precautions", "clinical_pharmacology", "description"):
        text = (fda_label or {}).get(section, "")
        if text:
            section_texts.append(f"=== {section.upper()} ===\n{text[:1200]}")

    if not section_texts:
        logger.warning("[SEStore] No FDA label sections for %s — skipping parse", drug_name)
        return None

    prompt = _build_gemini_prompt(drug_name, "\n\n".join(section_texts))

    try:
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 2000},
        )
        raw = (resp.text or "").strip()

        # Strip markdown fences if present
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```")[0].strip()
        elif raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            ).strip()

        effects_list = json.loads(raw)

        # Validate — must be a non-empty list of dicts
        if not isinstance(effects_list, list) or not effects_list:
            logger.warning("[SEStore] Gemini returned empty/non-list for %s", drug_name)
            return None

    except Exception as exc:
        logger.warning("[SEStore] Gemini parse failed for %s: %s", drug_name, exc)
        return None

    # Group by frequency tier
    tiers: dict[str, Any] = {
        "very_common": {"label": "Very Common (>10%)",  "items": []},
        "common":      {"label": "Common (1-10%)",       "items": []},
        "uncommon":    {"label": "Uncommon (<1%)",        "items": []},
        "serious":     {"label": "Serious — Seek Immediate Medical Attention",
                        "items": [], "urgent": True},
    }

    boxed_warnings = []
    for effect in effects_list:
        if not isinstance(effect, dict):
            continue
        freq = effect.get("frequency", "common")
        if freq not in tiers:
            freq = "common"
        # Cap per tier at 10 items
        if len(tiers[freq]["items"]) < 10:
            tiers[freq]["items"].append(effect)

    # Also pull boxed warnings from FDA label directly (more reliable than AI)
    bw_text = (fda_label or {}).get("boxed_warning", "")
    if bw_text:
        boxed_warnings = [s.strip() for s in bw_text.split(".") if len(s.strip()) > 10][:3]

    # MOA from clinical_pharmacology or description
    moa_text = (fda_label or {}).get("clinical_pharmacology", "") or (fda_label or {}).get("description", "")
    sentences = [s.strip() + "." for s in moa_text.split(".") if len(s.strip()) > 15]
    moa_summary = " ".join(sentences[:2])[:300]
    moa_detail  = " ".join(sentences[:5])[:800]

    total = sum(len(t["items"]) for t in tiers.values())
    logger.info("[SEStore] Gemini extracted %d side effects for %s", total, drug_name)

    return {
        "drug_name":    drug_name,
        "generic_name": drug_name,
        "brand_names":  [],
        "side_effects": tiers,
        "boxed_warnings": boxed_warnings,
        "mechanism_of_action": {
            "summary":             moa_summary,
            "detail":              moa_detail,
            "pharmacologic_class": "",
            "molecular_targets":   [],
        },
        "sources":      [],
        "_from_gemini": True,
    }


# ---------------------------------------------------------------------------
# Main entry point used by orchestrator + API endpoints
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
      1. DB cache  → fast (< 10ms)
      2. Gemini parse of FDA label + store to DB
      3. None (caller falls back to heuristic regex parser)
    """
    if not drug_name:
        return None

    # 1. DB lookup
    cached = get_from_db(drug_name)
    if cached:
        logger.info("[SEStore] DB cache hit for %s", drug_name)
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
        openfda  = raw_label.get("openfda", {})
        set_id   = (openfda.get("set_id") or [None])[0]
        eff_date = raw_label.get("effective_time", "")
        label_date = (
            f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}"
            if eff_date and len(eff_date) >= 8 else ""
        )
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

    # 3. Store to DB (don't let a failure crash the request)
    try:
        store_to_db(drug_name, parsed)
    except Exception as exc:
        logger.warning("[SEStore] Background store failed for %s: %s", drug_name, exc)

    return parsed
