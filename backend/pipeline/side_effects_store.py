"""
Side Effects Store — PostgreSQL-backed persistent drug side effect data.

Architecture:
  - drug_se_meta:     one row per drug (metadata, MOA, sources)
  - drug_side_effects: one row per (drug, effect) with rich clinical fields

Per-effect fields:
  display_name, frequency_category, frequency_percent, confidence_score,
  severity, patient_description, onset_days, resolution_days, management,
  red_flag, red_flag_reason, evidence_tier, source_section, source_quote

Confidence scoring:
  1.0  — exact percentage stated in FDA label  ("30% of patients")
  0.75 — strong frequency qualifier ("most common", "frequently")
  0.5  — weak qualifier ("may cause", "can cause", inferred tier)
  0.25 — missing or highly uncertain

Frequency categories (normalized):
  very_common  > 10%
  common       1–10%
  uncommon     0.1–1%
  rare         < 0.1%
  serious      life-threatening (regardless of frequency)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("rxbuddy.pipeline.se_store")

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def _database_url() -> str | None:
    return os.getenv("DATABASE_URL", "").strip() or None


def _get_engine():
    try:
        from sqlalchemy import create_engine
        url = _database_url()
        return create_engine(url, future=True, pool_pre_ping=True) if url else None
    except Exception as exc:
        logger.warning("[SEStore] Could not create engine: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Table creation / migration — runs once per process
# ---------------------------------------------------------------------------

_TABLES_CREATED = False

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
    overall_confidence  FLOAT DEFAULT 0.5,
    parsed_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

_CREATE_SE_TABLE = """
CREATE TABLE IF NOT EXISTS drug_side_effects (
    id                  SERIAL PRIMARY KEY,
    drug_generic_name   VARCHAR(255) NOT NULL,
    display_name        VARCHAR(500) NOT NULL,
    frequency_category  VARCHAR(20),
    frequency_percent   FLOAT,
    confidence_score    FLOAT DEFAULT 0.5,
    severity            VARCHAR(20),
    patient_description TEXT,
    onset_days          INT,
    resolution_days     INT,
    management          VARCHAR(100),
    red_flag            BOOLEAN DEFAULT FALSE,
    red_flag_reason     TEXT,
    evidence_tier       VARCHAR(20) DEFAULT 'label',
    source_section      VARCHAR(100),
    source_quote        TEXT,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(drug_generic_name, display_name)
);
"""

# Columns added in upgrades — safe to run on existing tables
_MIGRATIONS = [
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS confidence_score FLOAT DEFAULT 0.5;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS source_section VARCHAR(100);",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS source_quote TEXT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS frequency_category VARCHAR(20);",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS onset_days INT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS resolution_days INT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS patient_description TEXT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS management VARCHAR(100);",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS red_flag BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS red_flag_reason TEXT;",
    "ALTER TABLE drug_side_effects ADD COLUMN IF NOT EXISTS frequency_percent FLOAT;",
    "ALTER TABLE drug_se_meta ADD COLUMN IF NOT EXISTS overall_confidence FLOAT DEFAULT 0.5;",
]


def ensure_tables() -> bool:
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
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
        _TABLES_CREATED = True
        logger.info("[SEStore] Tables ready")
        return True
    except Exception as exc:
        logger.warning("[SEStore] ensure_tables failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

# Canonical deduplication map: lower-case variant → canonical display name
_SYNONYM_MAP: dict[str, str] = {
    "feeling sick": "Nausea",
    "sick feeling": "Nausea",
    "nauseous": "Nausea",
    "head pain": "Headache",
    "headpain": "Headache",
    "cephalalgia": "Headache",
    "stomach upset": "Upset Stomach",
    "stomach ache": "Stomach Pain",
    "abdominal pain": "Stomach Pain",
    "belly pain": "Stomach Pain",
    "loose stools": "Diarrhea",
    "loose bowels": "Diarrhea",
    "bowel disorder": "Diarrhea",
    "throwing up": "Vomiting",
    "feeling tired": "Fatigue",
    "tiredness": "Fatigue",
    "exhaustion": "Fatigue",
    "feeling dizzy": "Dizziness",
    "light-headedness": "Dizziness",
    "lightheadedness": "Dizziness",
    "vertigo": "Dizziness",
    "dry skin": "Skin Dryness",
    "itching": "Itching (Pruritus)",
    "pruritus": "Itching (Pruritus)",
    "rash": "Skin Rash",
    "skin rash": "Skin Rash",
    "skin reaction": "Skin Rash",
    "runny nose": "Runny Nose (Rhinorrhea)",
    "rhinorrhea": "Runny Nose (Rhinorrhea)",
    "insomnia": "Trouble Sleeping",
    "sleep disorder": "Trouble Sleeping",
    "difficulty sleeping": "Trouble Sleeping",
    "anxiety": "Anxiety",
    "nervousness": "Anxiety",
    "back ache": "Back Pain",
    "backache": "Back Pain",
    "muscular pain": "Muscle Pain (Myalgia)",
    "myalgia": "Muscle Pain (Myalgia)",
    "muscle ache": "Muscle Pain (Myalgia)",
    "muscle cramps": "Muscle Cramps",
    "cramps": "Muscle Cramps",
    "hot flash": "Hot Flashes",
    "hot flushes": "Hot Flashes",
    "flushing": "Flushing",
    "increased sweating": "Sweating",
    "diaphoresis": "Sweating",
    "weight gain": "Weight Gain",
    "weight increase": "Weight Gain",
    "weight loss": "Weight Loss",
    "weight decrease": "Weight Loss",
    "blurred vision": "Blurred Vision",
    "vision blurred": "Blurred Vision",
    "palpitations": "Heart Palpitations",
    "rapid heartbeat": "Heart Palpitations",
    "tachycardia": "Fast Heart Rate",
    "edema": "Swelling (Edema)",
    "swelling": "Swelling (Edema)",
    "peripheral edema": "Leg Swelling",
    "constipation": "Constipation",
    "flatulence": "Gas (Flatulence)",
    "gas": "Gas (Flatulence)",
    "bloating": "Bloating",
    "abdominal bloating": "Bloating",
    "dry mouth": "Dry Mouth",
    "xerostomia": "Dry Mouth",
    "loss of appetite": "Loss of Appetite",
    "decreased appetite": "Loss of Appetite",
    "anorexia": "Loss of Appetite",
    "decreased libido": "Decreased Sex Drive",
    "reduced libido": "Decreased Sex Drive",
    "sexual dysfunction": "Sexual Dysfunction",
    "urinary tract infection": "Urinary Tract Infection (UTI)",
    "uti": "Urinary Tract Infection (UTI)",
    "upper respiratory infection": "Upper Respiratory Infection",
    "uri": "Upper Respiratory Infection",
}


def _normalize_display_name(name: str) -> str:
    """Map synonyms to canonical names and title-case the result."""
    clean = name.strip().lower()
    return _SYNONYM_MAP.get(clean, name.strip().title())


def _normalize_frequency(
    freq_category: str | None,
    freq_percent: float | None,
) -> tuple[str, float | None, float]:
    """
    Normalize frequency to a canonical category + confidence score.

    Returns (category, percent, confidence_score).
    """
    # If we have an exact percent, derive category from it
    if freq_percent is not None:
        try:
            pct = float(freq_percent)
        except (TypeError, ValueError):
            pct = None
        if pct is not None:
            if pct > 10:
                cat = "very_common"
            elif pct >= 1:
                cat = "common"
            elif pct >= 0.1:
                cat = "uncommon"
            else:
                cat = "rare"
            return cat, pct, 1.0  # high confidence — exact number

    # No exact percent — use declared category + infer confidence
    cat_map = {
        "very_common": ("very_common", 0.75),
        "common":      ("common",      0.5),
        "uncommon":    ("uncommon",    0.5),
        "rare":        ("rare",        0.5),
        "serious":     ("serious",     0.75),  # severity, not freq
    }
    if freq_category and freq_category.lower() in cat_map:
        cat, conf = cat_map[freq_category.lower()]
        return cat, freq_percent, conf

    return "common", freq_percent, 0.25  # unknown — low confidence


def _deduplicate_effects(effects: list[dict]) -> list[dict]:
    """
    Remove semantic duplicates. Keeps the highest-confidence version.
    Uses _SYNONYM_MAP normalization + exact canonical-name dedup.
    """
    seen: dict[str, dict] = {}  # canonical_name → best effect dict
    for effect in effects:
        canonical = _normalize_display_name(effect.get("display_name", ""))
        if not canonical:
            continue
        existing = seen.get(canonical)
        if existing is None:
            seen[canonical] = {**effect, "display_name": canonical}
        else:
            # Keep whichever has higher confidence
            new_conf = effect.get("confidence_score", 0.25)
            old_conf = existing.get("confidence_score", 0.25)
            if new_conf > old_conf:
                seen[canonical] = {**effect, "display_name": canonical}
    return list(seen.values())


def _validate_effect_schema(effect: dict) -> dict:
    """
    Enforce strict schema on a single effect dict.
    Fills in defaults for missing fields; returns the cleaned dict.
    """
    freq_cat_raw = str(effect.get("frequency_category") or effect.get("frequency") or "")
    freq_pct_raw = effect.get("frequency_percent")
    freq_cat, freq_pct, conf = _normalize_frequency(freq_cat_raw, freq_pct_raw)

    # Allow Gemini-provided confidence to upgrade, never downgrade
    gemini_conf = effect.get("confidence_score")
    if isinstance(gemini_conf, (int, float)) and 0 <= gemini_conf <= 1:
        conf = max(conf, float(gemini_conf))

    mgmt = str(effect.get("management") or "monitor").lower()
    if mgmt not in ("monitor", "manage_at_home", "contact_doctor"):
        mgmt = "contact_doctor" if effect.get("red_flag") else "monitor"

    sev = str(effect.get("severity") or "mild").lower()
    if sev not in ("mild", "moderate", "severe"):
        sev = "severe" if freq_cat == "serious" else "mild"

    # Sanitize source_quote — max 25 words
    sq = str(effect.get("source_quote") or "")
    if sq:
        words = sq.split()
        sq = " ".join(words[:25]) + ("..." if len(words) > 25 else "")

    return {
        "display_name":       _normalize_display_name(str(effect.get("display_name") or "")),
        "frequency_category": freq_cat,
        "frequency_percent":  freq_pct,
        "confidence_score":   round(conf, 2),
        "severity":           sev,
        "patient_description": str(effect.get("patient_description") or "")[:1000],
        "onset_days":         _safe_int(effect.get("onset_days")),
        "resolution_days":    _safe_int(effect.get("resolution_days")),
        "management":         mgmt,
        "red_flag":           bool(effect.get("red_flag", False)),
        "red_flag_reason":    str(effect.get("red_flag_reason") or "")[:500] or None,
        "evidence_tier":      str(effect.get("evidence_tier") or "label"),
        "source_section":     str(effect.get("source_section") or "Adverse Reactions")[:100],
        "source_quote":       sq or None,
    }


def _safe_int(val: Any) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _overall_confidence(effects: list[dict]) -> float:
    """Average confidence across all effects; 0.5 baseline if no effects."""
    scores = [e.get("confidence_score", 0.5) for e in effects if e]
    return round(sum(scores) / len(scores), 2) if scores else 0.5


# ---------------------------------------------------------------------------
# DB read
# ---------------------------------------------------------------------------

def get_from_db(drug_name: str) -> dict | None:
    """Return structured side effects from DB, or None if not found."""
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
                text("SELECT * FROM drug_se_meta WHERE drug_generic_name = :n"),
                {"n": key},
            ).mappings().first()
            logger.info("[SEStore] get_from_db(%s) — meta row: %s", key,
                        dict(meta) if meta else None)
            if not meta:
                return None

            rows = conn.execute(
                text("SELECT * FROM drug_side_effects WHERE drug_generic_name = :n ORDER BY id"),
                {"n": key},
            ).mappings().all()

        # Group into frequency tiers
        tiers: dict[str, Any] = {
            "very_common": {"label": "Very Common (>10%)",  "items": []},
            "common":      {"label": "Common (1–10%)",       "items": []},
            "uncommon":    {"label": "Uncommon (0.1–1%)",    "items": []},
            "rare":        {"label": "Rare (<0.1%)",          "items": []},
            "serious":     {"label": "Serious — Seek Medical Attention",
                            "items": [], "urgent": True},
        }
        all_effects = []
        for row in rows:
            freq = row["frequency_category"] or "common"
            if freq not in tiers:
                freq = "common"
            rich = {
                "display_name":        row["display_name"],
                "frequency_category":  freq,
                "frequency_percent":   row["frequency_percent"],
                "confidence_score":    row["confidence_score"] or 0.5,
                "severity":            row["severity"] or "mild",
                "patient_description": row["patient_description"] or "",
                "onset_days":          row["onset_days"],
                "resolution_days":     row["resolution_days"],
                "management":          row["management"] or "monitor",
                "red_flag":            bool(row["red_flag"]),
                "red_flag_reason":     row["red_flag_reason"] or "",
                "evidence_tier":       row["evidence_tier"] or "label",
                "source_section":      row["source_section"] or "Adverse Reactions",
                "source_quote":        row["source_quote"] or "",
                "data_source":         "FDA Label (DailyMed)",
            }
            tiers[freq]["items"].append(rich)
            all_effects.append(rich)

        sources = []
        if meta["dailymed_url"]:
            sources.append({
                "id": 1, "name": f"DailyMed — {key.title()} label",
                "url": meta["dailymed_url"], "section": "ADVERSE REACTIONS",
                "last_updated": meta["label_date"] or "",
            })
        if meta["fda_url"]:
            sources.append({
                "id": 2, "name": "openFDA Drug Label API",
                "url": meta["fda_url"], "section": "adverse_reactions",
                "last_updated": meta["label_date"] or "",
            })

        has_red_flag = any(e["red_flag"] for e in all_effects)

        logger.info("[SEStore] DB hit for %s — %d effects, conf=%.2f, red_flag=%s",
                    key, len(all_effects), meta.get("overall_confidence", 0.5), has_red_flag)

        return {
            "drug":             key,
            "generic_name":     meta["generic_name_label"] or key,
            "brand_names":      list(meta["brand_names"] or []),
            "side_effects":     tiers,
            "boxed_warnings":   list(meta["boxed_warnings"] or []),
            "mechanism_of_action": {
                "summary":             meta["moa_summary"] or "",
                "detail":              meta["moa_detail"] or "",
                "pharmacologic_class": meta["pharmacologic_class"] or "",
                "molecular_targets":   list(meta["molecular_targets"] or []),
            },
            "sources":          sources,
            "overall_confidence": meta.get("overall_confidence") or 0.5,
            "overall_verdict":  "CONSULT_PHARMACIST" if has_red_flag else "CAUTION",
            "has_red_flag":     has_red_flag,
            "_from_db":         True,
        }

    except Exception as exc:
        logger.warning("[SEStore] get_from_db(%s) failed: %s", drug_name, exc)
        return None


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def store_to_db(drug_name: str, parsed: dict) -> bool:
    """
    Upsert parsed side effects into DB.
    Accepts both rich-dict items and plain-string items.
    """
    if not drug_name or not parsed:
        return False
    ensure_tables()
    engine = _get_engine()
    if not engine:
        return False

    from sqlalchemy import text
    key = drug_name.strip().lower()
    moa = parsed.get("mechanism_of_action", {})
    sources = parsed.get("sources", [])
    dailymed_url = next((s["url"] for s in sources if "dailymed" in s.get("url","").lower()), None)
    fda_url = next((s["url"] for s in sources if "api.fda.gov" in s.get("url","").lower()), None)
    label_date = sources[0].get("last_updated", "") if sources else ""

    # Flatten all effects for overall confidence calculation
    all_effects_flat: list[dict] = []

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO drug_se_meta
                    (drug_generic_name, brand_names, generic_name_label,
                     boxed_warnings, moa_summary, moa_detail,
                     pharmacologic_class, molecular_targets,
                     dailymed_url, fda_url, label_date, overall_confidence, parsed_at)
                VALUES
                    (:n, :brands, :glabel,
                     :boxed, :msum, :mdet,
                     :pclass, :targets,
                     :dmurl, :furl, :ldate, :oconf, NOW())
                ON CONFLICT (drug_generic_name) DO UPDATE SET
                    brand_names=EXCLUDED.brand_names, generic_name_label=EXCLUDED.generic_name_label,
                    boxed_warnings=EXCLUDED.boxed_warnings, moa_summary=EXCLUDED.moa_summary,
                    moa_detail=EXCLUDED.moa_detail, pharmacologic_class=EXCLUDED.pharmacologic_class,
                    molecular_targets=EXCLUDED.molecular_targets, dailymed_url=EXCLUDED.dailymed_url,
                    fda_url=EXCLUDED.fda_url, label_date=EXCLUDED.label_date,
                    overall_confidence=EXCLUDED.overall_confidence, parsed_at=NOW()
            """), {
                "n": key,
                "brands": parsed.get("brand_names", []),
                "glabel": parsed.get("generic_name", key),
                "boxed":  parsed.get("boxed_warnings", []),
                "msum":   moa.get("summary", ""),
                "mdet":   moa.get("detail", ""),
                "pclass": moa.get("pharmacologic_class", ""),
                "targets": moa.get("molecular_targets", []),
                "dmurl":  dailymed_url,
                "furl":   fda_url,
                "ldate":  label_date,
                "oconf":  0.5,  # will update below after computing
            })

            side_effects = parsed.get("side_effects", {})
            count = 0
            for freq_key, tier in side_effects.items():
                for item in tier.get("items", []):
                    if not item:
                        continue
                    # Validate + normalize the item
                    if isinstance(item, str):
                        item = {"display_name": item, "frequency_category": freq_key}
                    item["frequency_category"] = freq_key
                    clean = _validate_effect_schema(item)
                    all_effects_flat.append(clean)

                    conn.execute(text("""
                        INSERT INTO drug_side_effects
                            (drug_generic_name, display_name, frequency_category,
                             frequency_percent, confidence_score, severity,
                             patient_description, onset_days, resolution_days,
                             management, red_flag, red_flag_reason,
                             evidence_tier, source_section, source_quote, updated_at)
                        VALUES
                            (:n, :dn, :fcat,
                             :fpct, :conf, :sev,
                             :desc, :onset, :res,
                             :mgmt, :rflag, :rwhy,
                             :etier, :ssec, :squote, NOW())
                        ON CONFLICT (drug_generic_name, display_name) DO UPDATE SET
                            frequency_category=EXCLUDED.frequency_category,
                            frequency_percent=EXCLUDED.frequency_percent,
                            confidence_score=EXCLUDED.confidence_score,
                            severity=EXCLUDED.severity,
                            patient_description=EXCLUDED.patient_description,
                            onset_days=EXCLUDED.onset_days,
                            resolution_days=EXCLUDED.resolution_days,
                            management=EXCLUDED.management,
                            red_flag=EXCLUDED.red_flag,
                            red_flag_reason=EXCLUDED.red_flag_reason,
                            evidence_tier=EXCLUDED.evidence_tier,
                            source_section=EXCLUDED.source_section,
                            source_quote=EXCLUDED.source_quote,
                            updated_at=NOW()
                    """), {
                        "n":      key,
                        "dn":     clean["display_name"][:500],
                        "fcat":   clean["frequency_category"],
                        "fpct":   clean["frequency_percent"],
                        "conf":   clean["confidence_score"],
                        "sev":    clean["severity"],
                        "desc":   clean["patient_description"],
                        "onset":  clean["onset_days"],
                        "res":    clean["resolution_days"],
                        "mgmt":   clean["management"],
                        "rflag":  clean["red_flag"],
                        "rwhy":   clean["red_flag_reason"],
                        "etier":  clean["evidence_tier"],
                        "ssec":   clean["source_section"],
                        "squote": clean["source_quote"],
                    })
                    count += 1

            # Update overall confidence now that we have all effects
            overall_conf = _overall_confidence(all_effects_flat)
            conn.execute(
                text("UPDATE drug_se_meta SET overall_confidence=:c WHERE drug_generic_name=:n"),
                {"c": overall_conf, "n": key},
            )

        logger.info("[SEStore] Stored %d effects for %s, overall_confidence=%.2f",
                    count, key, overall_conf)
        return True

    except Exception as exc:
        logger.warning("[SEStore] store_to_db(%s) failed: %s", drug_name, exc)
        return False


# ---------------------------------------------------------------------------
# Gemini parser — production-grade prompt
# ---------------------------------------------------------------------------

def _build_gemini_prompt(drug_name: str, label_text: str) -> str:
    return f"""You are a pharmaceutical NLP expert parsing FDA drug labels.

Extract ALL side effects from this label for "{drug_name}".

Return a JSON array. Each element MUST have exactly these fields:

{{
  "display_name":        "Patient-friendly name (e.g. Diarrhea, not Bowel disorder)",
  "frequency_category":  "very_common|common|uncommon|rare|serious",
  "frequency_percent":   30.0,        // exact number if stated, else null
  "confidence_score":    0.95,        // 1.0=exact%, 0.75=strong qualifier, 0.5=inferred, 0.25=guessed
  "severity":            "mild|moderate|severe",
  "patient_description": "1-2 plain English sentences describing what it feels like",
  "onset_days":          3,           // days after starting — integer, or null
  "resolution_days":     7,           // days until resolved — integer, or null
  "management":          "monitor|manage_at_home|contact_doctor",
  "red_flag":            false,       // true ONLY if life-threatening or requires emergency care
  "red_flag_reason":     null,        // why urgent — string, or null if red_flag=false
  "evidence_tier":       "label",     // always "label" for FDA data
  "source_section":      "Adverse Reactions",  // exact section name from label
  "source_quote":        "exact quote from label (max 20 words)"
}}

FREQUENCY RULES (apply strictly):
  very_common  = >10% OR "most common" OR "frequently reported"
  common       = 1-10% OR "common" OR listed without qualifier in adverse reactions
  uncommon     = 0.1-1% OR "uncommon" OR "infrequent" OR "rare" (< 1%)
  rare         = <0.1% OR "very rare" OR "isolated reports"
  serious      = life-threatening, regardless of frequency (cardiac arrest, liver failure, etc.)

CONFIDENCE RULES:
  1.0  = exact % stated: "occurred in 30% of patients"
  0.75 = strong qualifier: "most common", "frequently", "commonly observed"
  0.5  = listed without qualifier in adverse reactions section
  0.25 = inferred, guessed, or from unrelated context

CRITICAL:
- Do NOT invent effects not in the label
- NEVER put death, overdose, addiction, cardiac arrest, respiratory failure in common/very_common
- If % is stated, always set frequency_percent and confidence_score=1.0
- Return ONLY the JSON array, no markdown, no explanations

FDA LABEL — {drug_name.upper()}:
{label_text}"""


def parse_label_with_gemini(drug_name: str, fda_label: dict) -> dict | None:
    """
    Parse an FDA label into rich structured side effects using Gemini.
    Returns standard dict or None if Gemini is unavailable.

    Logs: Gemini latency, parse success/failure, fallback usage.
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        logger.warning("[SEStore] GEMINI_API_KEY not set — skipping Gemini parse for %s", drug_name)
        return None

    try:
        from google import genai
        client = genai.Client(api_key=gemini_key)
    except Exception as exc:
        logger.error("[SEStore] Gemini init failed: %s", exc)
        return None

    # Build label context — sanitize surrogates before embedding in prompt
    def _san(t: str) -> str:
        return t.encode("utf-8", errors="replace").decode("utf-8")

    section_texts = []
    for sec in ("adverse_reactions", "warnings", "boxed_warning",
                "warnings_and_precautions", "clinical_pharmacology", "description"):
        txt = _san((fda_label or {}).get(sec, ""))
        if txt:
            section_texts.append(f"=== {sec.upper()} ===\n{txt[:1200]}")

    if not section_texts:
        logger.warning("[SEStore] No label sections for %s — cannot parse", drug_name)
        return None

    prompt = _build_gemini_prompt(drug_name, "\n\n".join(section_texts))

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        latency_ms = int((time.time() - t0) * 1000)
        raw = (resp.text or "").strip()
        logger.info("[SEStore] Gemini responded in %dms for %s (%d chars)",
                    latency_ms, drug_name, len(raw))
    except Exception as exc:
        latency_ms = int((time.time() - t0) * 1000)
        logger.error("[SEStore] Gemini call failed for %s after %dms: %s",
                     drug_name, latency_ms, exc)
        return None

    # Log raw response for debugging
    logger.info("[SEStore] Gemini raw response for %s (first 500 chars): %.500s",
                drug_name, raw)

    # Strip markdown fences
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```")[0].strip()
    elif raw.startswith("```"):
        raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```")).strip()

    # If raw is empty, resp.text may need to come from candidates instead
    if not raw:
        try:
            raw = resp.candidates[0].content.parts[0].text.strip()
            logger.info("[SEStore] Fell back to candidates path — got %d chars", len(raw))
        except Exception:
            pass

    try:
        effects_list = json.loads(raw)
        if not isinstance(effects_list, list):
            raise ValueError("Response is not a JSON array")
        if not effects_list:
            raise ValueError("Response array is empty")
    except Exception as exc:
        logger.error("[SEStore] Gemini JSON parse failed for %s: %s — raw: %.500s",
                     drug_name, exc, raw)
        return None

    # Validate, normalize, and deduplicate
    validated = []
    for item in effects_list:
        if isinstance(item, dict) and item.get("display_name"):
            validated.append(_validate_effect_schema(item))
    validated = _deduplicate_effects(validated)

    if not validated:
        logger.warning("[SEStore] Gemini returned no valid effects for %s after validation", drug_name)
        return None

    # Group into tiers
    tiers: dict[str, Any] = {
        "very_common": {"label": "Very Common (>10%)",  "items": []},
        "common":      {"label": "Common (1–10%)",       "items": []},
        "uncommon":    {"label": "Uncommon (0.1–1%)",    "items": []},
        "rare":        {"label": "Rare (<0.1%)",          "items": []},
        "serious":     {"label": "Serious — Seek Medical Attention",
                        "items": [], "urgent": True},
    }
    for effect in validated:
        freq = effect["frequency_category"]
        if freq in tiers and len(tiers[freq]["items"]) < 10:
            tiers[freq]["items"].append(effect)

    # Boxed warnings direct from label (more reliable than AI)
    bw_text = (fda_label or {}).get("boxed_warning", "")
    boxed_warnings = [s.strip() for s in bw_text.split(".") if len(s.strip()) > 10][:3] if bw_text else []

    # MOA from label sections
    moa_raw = (fda_label or {}).get("clinical_pharmacology", "") or (fda_label or {}).get("description", "")
    sents = [s.strip() + "." for s in moa_raw.split(".") if len(s.strip()) > 15]
    moa_summary = " ".join(sents[:2])[:300]
    moa_detail  = " ".join(sents[:5])[:800]

    overall_conf = _overall_confidence(validated)
    total = sum(len(t["items"]) for t in tiers.values())

    logger.info("[SEStore] Gemini parse OK for %s — %d effects, conf=%.2f, latency=%dms",
                drug_name, total, overall_conf, latency_ms)

    return {
        "drug":           drug_name,
        "generic_name":   drug_name,
        "brand_names":    [],
        "side_effects":   tiers,
        "boxed_warnings": boxed_warnings,
        "mechanism_of_action": {
            "summary":             moa_summary,
            "detail":              moa_detail,
            "pharmacologic_class": "",
            "molecular_targets":   [],
        },
        "sources":          [],
        "overall_confidence": overall_conf,
        "overall_verdict":  "CONSULT_PHARMACIST" if any(e["red_flag"] for e in validated) else "CAUTION",
        "has_red_flag":     any(e["red_flag"] for e in validated),
        "_from_gemini":     True,
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

    Full waterfall:
      1. DB cache              — < 10ms, any drug already seen
      2. Hardcoded fallback    — top ~100 drugs (checked by caller in
         parse_structured_side_effects, but also checked here)
      3. DailyMed structured   — clean table data from SPL XML
      4. Gemini parse          — AI parse of FDA label prose
      5. Heuristic regex parse — no AI, lower confidence
      6. Drug-class fallback   — generic class data
      7. Store result to DB    — so Tier 1 catches it next time

    Always logs: cache hit/miss, tier used, latency.
    Returns None only if every tier fails.
    """
    if not drug_name:
        return None

    # ── Tier 1: DB cache ─────────────────────────────────────────────────────
    t0 = time.time()
    cached = get_from_db(drug_name)
    if cached:
        logger.info("[SEStore] Cache HIT for %s (%.0fms)", drug_name, (time.time()-t0)*1000)
        return cached

    logger.info("[SEStore] Cache MISS for %s", drug_name)

    # ── Tier 2: Hardcoded class fallback ─────────────────────────────────────
    fallback = _get_class_fallback(drug_name)
    if fallback:
        logger.info("[SEStore] Hardcoded fallback for %s — storing to DB", drug_name)
        try:
            store_to_db(drug_name, fallback)
        except Exception as exc:
            logger.warning("[SEStore] Store failed for %s: %s", drug_name, exc)
        return fallback

    # ── Tier 3: DailyMed structured API (clean table data) ───────────────────
    if dailymed_setid:
        try:
            from pipeline.api_layer import fetch_dailymed_structured_sections, parse_dailymed_structured
            import asyncio
            structured_sections = await fetch_dailymed_structured_sections(dailymed_setid)
            if structured_sections:
                structured_sections["_setid"] = dailymed_setid
                parsed_dm = parse_dailymed_structured(drug_name, structured_sections)
                if parsed_dm:
                    logger.info("[SEStore] DailyMed structured succeeded for %s", drug_name)
                    try:
                        store_to_db(drug_name, parsed_dm)
                    except Exception as exc:
                        logger.warning("[SEStore] Store failed for %s: %s", drug_name, exc)
                    return parsed_dm
        except Exception as e:
            logger.warning("[SEStore] DailyMed structured fetch failed for %s: %s", drug_name, e)

    # ── Tier 4: Gemini parse of FDA label ────────────────────────────────────
    if fda_label:
        parsed = parse_label_with_gemini(drug_name, fda_label)

        if parsed:
            # Enrich with raw openFDA metadata
            if raw_label:
                openfda   = raw_label.get("openfda", {})
                set_id    = (openfda.get("set_id") or [None])[0]
                eff_date  = raw_label.get("effective_time", "")
                label_date = (
                    f"{eff_date[:4]}-{eff_date[4:6]}-{eff_date[6:8]}"
                    if eff_date and len(eff_date) >= 8 else ""
                )
                parsed["brand_names"]  = list(openfda.get("brand_name", []))[:5]
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

            # Store to DB (Tier 7)
            try:
                store_to_db(drug_name, parsed)
            except Exception as exc:
                logger.warning("[SEStore] Store failed for %s: %s", drug_name, exc)

            return parsed

        logger.warning("[SEStore] Gemini failed for %s — trying heuristic", drug_name)

    # ── Tier 5: Heuristic regex parse of the FDA label (no AI) ───────────────
    if fda_label:
        heuristic = _quick_heuristic_parse(drug_name, fda_label)
        if heuristic:
            return heuristic

    logger.warning("[SEStore] All parsers failed for %s — no data available", drug_name)
    return None


# ---------------------------------------------------------------------------
# Step 3: Heuristic label parse (no AI, regex-based)
# ---------------------------------------------------------------------------

def _quick_heuristic_parse(drug_name: str, fda_label: dict) -> dict | None:
    """
    Fast regex extraction of side effects directly from FDA label text.
    Used when Gemini is unavailable. Confidence is lower (0.4) but data is real.
    Returns standard tier dict or None if no useful data could be extracted.
    """
    import re as _re

    adverse_text = (fda_label or {}).get("adverse_reactions", "")
    warnings_text = ((fda_label or {}).get("warnings_and_precautions", "")
                     or (fda_label or {}).get("warnings", ""))
    boxed_text = (fda_label or {}).get("boxed_warning", "")

    if not adverse_text and not warnings_text:
        return None

    tiers: dict = {
        "very_common": {"label": "Very Common (>10%)",  "items": []},
        "common":      {"label": "Common (1–10%)",       "items": []},
        "uncommon":    {"label": "Uncommon (0.1–1%)",    "items": []},
        "rare":        {"label": "Rare (<0.1%)",          "items": []},
        "serious":     {"label": "Serious — Seek Medical Attention", "items": [], "urgent": True},
    }

    # Serious-sounding terms detected via keyword scan of warnings text
    serious_keywords = {
        "liver failure", "hepatic failure", "anaphylaxis", "anaphylactic",
        "angioedema", "suicidal", "serotonin syndrome", "lactic acidosis",
        "rhabdomyolysis", "agranulocytosis", "aplastic anemia",
        "stevens-johnson", "toxic epidermal", "QT prolongation",
        "pancreatitis", "renal failure", "kidney failure",
    }

    # Extract named effects from adverse reactions text by splitting on delimiters
    seen: set[str] = set()
    items_added = 0

    for text in (adverse_text, warnings_text):
        if not text or items_added >= 20:
            break
        parts = _re.split(r"[;,\n\u2022\u25cf\u00b7]", text)
        for part in parts:
            part = part.strip()
            # Skip very long strings (descriptions), very short, or obvious headers
            if len(part) > 55 or len(part) < 3:
                continue
            # Remove leading bullets/numbers/punctuation
            part = _re.sub(r"^[\s\-\•\*\d\.\)\(]+", "", part).strip()
            # Skip % lines and parenthetical-only strings
            if not part or _re.match(r"^\d", part) or part.startswith("("):
                continue
            # Skip anything that looks like a sentence (contains verb markers)
            if len(part.split()) > 5:
                continue

            key = part.lower()
            if key in seen:
                continue
            seen.add(key)

            canonical = _normalize_display_name(part)

            # Classify as serious if any serious keyword found in warnings_text
            is_serious = any(kw in key for kw in serious_keywords)
            freq = "serious" if is_serious else "common"

            tiers[freq]["items"].append({
                "display_name":       canonical,
                "frequency_category": freq,
                "frequency_percent":  None,
                "confidence_score":   0.4,
                "severity":           "severe" if is_serious else "mild",
                "patient_description": "",
                "onset_days":         None,
                "resolution_days":    None,
                "management":         "contact_doctor" if is_serious else "monitor",
                "red_flag":           is_serious,
                "red_flag_reason":    None,
                "evidence_tier":      "label",
                "source_section":     "Adverse Reactions",
                "source_quote":       None,
            })
            items_added += 1

    # Boxed warnings
    boxed_warnings = []
    if boxed_text:
        boxed_warnings = [s.strip() for s in boxed_text.split(".") if len(s.strip()) > 10][:3]

    if not tiers["common"]["items"] and not tiers["serious"]["items"]:
        return None

    total = sum(len(t["items"]) for t in tiers.values())
    logger.info("[SEStore] Heuristic parse for %s: %d effects extracted", drug_name, total)

    return {
        "drug":            drug_name,
        "generic_name":    drug_name,
        "brand_names":     [],
        "side_effects":    tiers,
        "boxed_warnings":  boxed_warnings,
        "mechanism_of_action": {
            "summary": "", "detail": "",
            "pharmacologic_class": "", "molecular_targets": [],
        },
        "sources": [{
            "id": 1, "name": "FDA Label (heuristic parse)",
            "url": f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{drug_name}"&limit=1',
            "section": "Adverse Reactions", "last_updated": "",
        }],
        "overall_confidence": 0.4,
        "overall_verdict":    "CONSULT_PHARMACIST" if tiers["serious"]["items"] else "CAUTION",
        "has_red_flag":       bool(tiers["serious"]["items"]),
        "_fallback":          True,
        "_fallback_source":   "heuristic_label_parse",
    }


# ---------------------------------------------------------------------------
# Step 4: Drug-class hardcoded fallbacks — verified clinical facts
# ---------------------------------------------------------------------------

# Alias map: brand/common names → generic key
_DRUG_ALIASES: dict[str, str] = {
    # Analgesics / antipyretics
    "tylenol":       "acetaminophen",
    "advil":         "ibuprofen",
    "motrin":        "ibuprofen",
    "aleve":         "naproxen",
    "naprosyn":      "naproxen",
    "bayer":         "aspirin",
    "ecotrin":       "aspirin",
    # Statins
    "lipitor":       "atorvastatin",
    "crestor":       "rosuvastatin",
    "zocor":         "simvastatin",
    # Antidepressants / anxiolytics
    "zoloft":        "sertraline",
    "prozac":        "fluoxetine",
    "sarafem":       "fluoxetine",
    "lexapro":       "escitalopram",
    "wellbutrin":    "bupropion",
    "zyban":         "bupropion",
    "aplenzin":      "bupropion",
    "effexor":       "venlafaxine",
    "cymbalta":      "duloxetine",
    "buspar":        "buspirone",
    # Benzodiazepines
    "xanax":         "alprazolam",
    "klonopin":      "clonazepam",
    "ativan":        "lorazepam",
    "valium":        "diazepam",
    # ADHD medications
    "ritalin":       "methylphenidate",
    "concerta":      "methylphenidate",
    "quillivant":    "methylphenidate",
    "adderall":      "amphetamine",
    "dexedrine":     "amphetamine",
    "strattera":     "atomoxetine",
    # Antihistamines
    "benadryl":      "diphenhydramine",
    "unisom":        "diphenhydramine",
    "claritin":      "loratadine",
    "zyrtec":        "cetirizine",
    "allegra":       "fexofenadine",
    # GI / acid
    "glucophage":    "metformin",
    "prilosec":      "omeprazole",
    "nexium":        "esomeprazole",
    "protonix":      "pantoprazole",
    "pepcid":        "famotidine",
    # BP / cardiac
    "prinivil":      "lisinopril",
    "zestril":       "lisinopril",
    "norvasc":       "amlodipine",
    "cozaar":        "losartan",
    "hyzaar":        "losartan",
    "lopressor":     "metoprolol",
    "toprol":        "metoprolol",
    "toprol xl":     "metoprolol",
    "lanoxin":       "digoxin",
    "pacerone":      "amiodarone",
    "cordarone":     "amiodarone",
    "coumadin":      "warfarin",
    "jantoven":      "warfarin",
    "eliquis":       "apixaban",
    "xarelto":       "rivaroxaban",
    "plavix":        "clopidogrel",
    "microzide":     "hydrochlorothiazide",
    "hctz":          "hydrochlorothiazide",
    "lasix":         "furosemide",
    "aldactone":     "spironolactone",
    # Thyroid / endocrine
    "synthroid":     "levothyroxine",
    "levoxyl":       "levothyroxine",
    "jardiance":     "empagliflozin",
    "glucotrol":     "glipizide",
    # Antibiotics
    "zithromax":     "azithromycin",
    "z-pak":         "azithromycin",
    "cipro":         "ciprofloxacin",
    "vibramycin":    "doxycycline",
    "doryx":         "doxycycline",
    # Respiratory
    "ventolin":      "albuterol",
    "proventil":     "albuterol",
    "singulair":     "montelukast",
    "flonase":       "fluticasone",
    "flovent":       "fluticasone",
    # Neuro / psych
    "lyrica":        "pregabalin",
    "neurontin":     "gabapentin",
    "topamax":       "topiramate",
    "depakote":      "valproate",
    "depakene":      "valproate",
    "lamictal":      "lamotrigine",
    "risperdal":     "risperidone",
    "abilify":       "aripiprazole",
    "zyprexa":       "olanzapine",
    "seroquel":      "quetiapine",
    "ambien":        "zolpidem",
    "desyrel":       "trazodone",
    "oleptro":       "trazodone",
    # Pain / opioids
    "ultram":        "tramadol",
    "vicodin":       "hydrocodone",
    "norco":         "hydrocodone",
    "lortab":        "hydrocodone",
    "percocet":      "oxycodone",
    "oxycontin":     "oxycodone",
    "roxicodone":    "oxycodone",
    "ms contin":     "morphine",
    "msir":          "morphine",
    "kadian":        "morphine",
    # Anti-inflammatory / immune
    "medrol":        "methylprednisolone",
    "deltasone":     "prednisone",
    "rheumatrex":    "methotrexate",
    "plaquenil":     "hydroxychloroquine",
    "humira":        "adalimumab",
    # Urology / other
    "flomax":        "tamsulosin",
    "viagra":        "sildenafil",
    "revatio":       "sildenafil",
    "propecia":      "finasteride",
    "proscar":       "finasteride",
    "zofran":        "ondansetron",
    "flexeril":      "cyclobenzaprine",
    # BP2 / clonidine
    "catapres":      "clonidine",
    "intuniv":       "guanfacine",
    "tenex":         "guanfacine",
}

_DRUG_CLASS_FALLBACKS: dict[str, list[dict]] = {
    "metformin": [
        {"display_name": "Nausea",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Feeling sick to your stomach, especially when first starting. Usually improves after a few weeks."},
        {"display_name": "Diarrhea",        "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Loose stools. Taking metformin with food usually helps."},
        {"display_name": "Stomach Upset",   "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Abdominal discomfort, especially when starting treatment."},
        {"display_name": "Vomiting",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Contact your doctor if persistent."},
        {"display_name": "Metallic Taste",  "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "An unpleasant metallic taste in the mouth. Usually temporary."},
        {"display_name": "Loss of Appetite","frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Vitamin B12 Deficiency", "frequency_category": "uncommon", "severity": "mild", "management": "monitor", "red_flag": False, "patient_description": "Long-term use may reduce B12 absorption. Your doctor may check your B12 levels."},
        {"display_name": "Lactic Acidosis", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True,  "red_flag_reason": "Rare but life-threatening buildup of lactic acid. Seek emergency care for muscle pain, difficulty breathing, or unusual weakness."},
    ],
    "acetaminophen": [
        {"display_name": "Nausea",            "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Mild stomach upset, especially on an empty stomach."},
        {"display_name": "Stomach Pain",      "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Liver Damage",      "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True,  "red_flag_reason": "Overdose or combining with alcohol can cause acute liver failure. Never exceed 4g/day total."},
        {"display_name": "Serious Skin Reactions", "frequency_category": "rare", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening skin reactions (DRESS, Stevens-Johnson Syndrome). Stop immediately and seek care."},
    ],
    "ibuprofen": [
        {"display_name": "Stomach Upset",    "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",           "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Heartburn",        "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Stomach Bleeding", "frequency_category": "serious",     "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "NSAIDs can cause GI bleeding. Risk higher with long-term use or in older adults."},
        {"display_name": "Kidney Problems",  "frequency_category": "serious",     "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "NSAIDs may worsen kidney function, especially with dehydration or pre-existing kidney disease."},
    ],
    "naproxen": [
        {"display_name": "Stomach Upset",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Heartburn",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "GI Bleeding",      "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "NSAIDs can cause stomach or intestinal bleeding."},
    ],
    "aspirin": [
        {"display_name": "Stomach Upset",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Heartburn",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "GI Bleeding",      "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Aspirin can cause stomach or intestinal bleeding, especially with long-term use."},
    ],
    "atorvastatin": [
        {"display_name": "Muscle Pain",      "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Soreness or weakness in muscles. Report to your doctor if severe."},
        {"display_name": "Headache",         "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",         "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Joint Pain",       "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Rhabdomyolysis",   "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare muscle breakdown that can damage kidneys. Seek care for severe muscle pain or dark urine."},
    ],
    "rosuvastatin": [
        {"display_name": "Muscle Pain",      "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Headache",         "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",           "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Rhabdomyolysis",   "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but serious muscle breakdown. Seek care for severe muscle pain or dark urine."},
    ],
    "lisinopril": [
        {"display_name": "Dry Cough",        "frequency_category": "very_common", "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "A persistent dry, tickling cough. Very common with ACE inhibitors — affects up to 20% of patients."},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",         "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",          "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "High Potassium",   "frequency_category": "uncommon",    "severity": "moderate", "management": "monitor",        "red_flag": False},
        {"display_name": "Angioedema",       "frequency_category": "serious",     "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Life-threatening swelling of face, lips, tongue, or throat. Stop immediately and seek emergency care."},
    ],
    "amlodipine": [
        {"display_name": "Leg Swelling",     "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Flushing",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Heart Palpitations", "frequency_category": "uncommon",  "severity": "mild",   "management": "monitor",        "red_flag": False},
    ],
    "sertraline": [
        {"display_name": "Nausea",             "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",           "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Insomnia",           "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dry Mouth",          "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sweating",           "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Sexual Dysfunction", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Suicidal Thoughts",  "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Antidepressants may increase suicidal thinking in children and young adults. Monitor closely, especially when starting treatment."},
        {"display_name": "Serotonin Syndrome", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous drug interaction causing agitation, rapid heart rate, high temperature. Seek emergency care immediately."},
    ],
    "escitalopram": [
        {"display_name": "Nausea",             "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Insomnia",           "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Dry Mouth",          "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sweating",           "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Sexual Dysfunction", "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Suicidal Thoughts",  "frequency_category": "serious",     "severity": "severe","management": "contact_doctor", "red_flag": True, "red_flag_reason": "Antidepressants may increase suicidal thinking in children and young adults. Monitor closely."},
        {"display_name": "QT Prolongation",    "frequency_category": "serious",     "severity": "severe","management": "contact_doctor", "red_flag": True, "red_flag_reason": "Escitalopram can prolong the QT interval. Tell your doctor about any heart rhythm problems."},
    ],
    "fluoxetine": [
        {"display_name": "Nausea",             "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Insomnia",           "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Headache",           "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sexual Dysfunction", "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Suicidal Thoughts",  "frequency_category": "serious",     "severity": "severe","management": "contact_doctor", "red_flag": True, "red_flag_reason": "Antidepressants may increase suicidal thinking in young adults. Monitor closely when starting."},
    ],
    "omeprazole": [
        {"display_name": "Headache",           "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",           "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",             "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain",       "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Magnesium Deficiency","frequency_category": "uncommon",   "severity": "mild", "management": "monitor",        "red_flag": False, "patient_description": "Long-term use may lower magnesium levels. Your doctor may monitor blood levels."},
        {"display_name": "C. diff Infection",  "frequency_category": "uncommon",    "severity": "moderate","management": "contact_doctor","red_flag": False, "patient_description": "PPIs can increase risk of C. difficile diarrhea. Contact your doctor for severe or persistent diarrhea."},
    ],
    "esomeprazole": [
        {"display_name": "Headache",    "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",    "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",      "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain","frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    "amoxicillin": [
        {"display_name": "Diarrhea",          "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",            "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain",      "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Rash",              "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "C. diff Infection", "frequency_category": "uncommon",    "severity": "moderate", "management": "contact_doctor", "red_flag": False},
        {"display_name": "Allergic Reaction (Anaphylaxis)", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Penicillin allergy can cause anaphylaxis — seek emergency care for breathing difficulty or facial swelling."},
    ],
    "levothyroxine": [
        {"display_name": "Heart Palpitations", "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Racing or irregular heartbeat if dose is too high."},
        {"display_name": "Tremors",            "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Insomnia",           "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Weight Loss",        "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Headache",           "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Chest Pain",         "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Excess thyroid hormone can stress the heart. Seek care for chest pain, shortness of breath, or irregular heartbeat."},
    ],
    "gabapentin": [
        {"display_name": "Drowsiness",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",       "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Coordination Problems", "frequency_category": "common", "severity": "mild",  "management": "monitor",        "red_flag": False},
        {"display_name": "Fatigue",         "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Swelling",        "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Respiratory Depression", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Especially dangerous when combined with opioids or CNS depressants. Seek emergency care for slow or difficult breathing."},
    ],
    # -----------------------------------------------------------------------
    # Antihistamines
    # -----------------------------------------------------------------------
    "diphenhydramine": [
        {"display_name": "Drowsiness",           "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Strong sedating effect — do not drive or operate machinery after taking Benadryl."},
        {"display_name": "Dry Mouth",            "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Very common — sip water or chew sugar-free gum to help."},
        {"display_name": "Dizziness",            "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation",         "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Blurred Vision",       "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Urinary Retention",    "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Difficulty urinating — particularly common in older adults and men with prostate issues."},
        {"display_name": "Confusion",            "frequency_category": "uncommon",    "severity": "moderate", "management": "contact_doctor", "red_flag": False, "patient_description": "Older adults are especially sensitive — diphenhydramine is listed on the Beers Criteria as inappropriate for elderly patients."},
    ],
    "loratadine": [
        {"display_name": "Headache",   "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Drowsiness", "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Less sedating than older antihistamines, but some people still feel drowsy."},
        {"display_name": "Dry Mouth",  "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",    "frequency_category": "common",   "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Nausea",     "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    "cetirizine": [
        {"display_name": "Drowsiness", "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "More sedating than loratadine for some people. Best taken at bedtime if it causes sleepiness."},
        {"display_name": "Dry Mouth",  "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",    "frequency_category": "common",   "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Headache",   "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",     "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    "fexofenadine": [
        {"display_name": "Headache",   "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",     "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",   "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",  "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    # -----------------------------------------------------------------------
    # Statins (additional)
    # -----------------------------------------------------------------------
    "simvastatin": [
        {"display_name": "Muscle Pain",    "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Aching or weakness in muscles, especially legs. Report to doctor if severe."},
        {"display_name": "Headache",       "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",         "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation",   "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Rhabdomyolysis", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Risk higher at 80 mg dose and with certain interacting drugs (e.g. amiodarone, verapamil). Seek care for severe muscle pain or dark urine."},
    ],
    # -----------------------------------------------------------------------
    # Blood pressure / cardiac (additional)
    # -----------------------------------------------------------------------
    "losartan": [
        {"display_name": "Dizziness",       "frequency_category": "common",   "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",         "frequency_category": "common",   "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "High Potassium",  "frequency_category": "uncommon", "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "ARBs can raise potassium levels. Your doctor may check your labs periodically."},
        {"display_name": "Back Pain",       "frequency_category": "common",   "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Angioedema",      "frequency_category": "serious",  "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare life-threatening swelling of face, lips, or throat. Seek emergency care immediately."},
    ],
    "metoprolol": [
        {"display_name": "Fatigue",          "frequency_category": "very_common", "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Feeling tired is very common with beta-blockers, especially when starting. Often improves over time."},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Slow Heart Rate",  "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Beta-blockers intentionally slow the heart. Contact your doctor if pulse drops below 50 beats per minute."},
        {"display_name": "Cold Hands/Feet",  "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Depression",       "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Shortness of Breath", "frequency_category": "serious",  "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can trigger bronchospasm — do not take if you have asthma or COPD without close monitoring."},
    ],
    "digoxin": [
        {"display_name": "Nausea",              "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",            "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",            "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Loss of Appetite",    "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Visual Changes (Yellow/Green Halos)", "frequency_category": "common", "severity": "mild", "management": "contact_doctor", "red_flag": False, "patient_description": "Seeing yellow-green halos around lights can be a sign of digoxin toxicity — contact your doctor."},
        {"display_name": "Digoxin Toxicity / Arrhythmias", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Digoxin has a narrow therapeutic window. Toxicity causes dangerous heart arrhythmias. Seek care for irregular heartbeat, confusion, or vision changes."},
    ],
    "amiodarone": [
        {"display_name": "Photosensitivity",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Skin becomes very sensitive to sunlight — use sunscreen and protective clothing."},
        {"display_name": "Nausea",                "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",               "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Tremors",               "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Thyroid Dysfunction",   "frequency_category": "common",      "severity": "moderate","management": "monitor",       "red_flag": False, "patient_description": "Can cause either overactive or underactive thyroid. Regular thyroid function tests are essential."},
        {"display_name": "Pulmonary Toxicity",    "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause lung damage. Seek care for progressive shortness of breath, cough, or fever."},
        {"display_name": "Liver Toxicity",        "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause liver damage. Tell your doctor about yellowing skin, dark urine, or severe abdominal pain."},
        {"display_name": "QT Prolongation",       "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Prolongs the QT interval which can trigger dangerous heart arrhythmias."},
    ],
    # -----------------------------------------------------------------------
    # Anticoagulants / antiplatelets
    # -----------------------------------------------------------------------
    "warfarin": [
        {"display_name": "Easy Bruising",    "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Bleeding (minor)", "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Minor bleeding (nosebleeds, prolonged cuts) is expected. Keep INR within target range."},
        {"display_name": "Hair Loss",        "frequency_category": "uncommon",    "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Nausea",           "frequency_category": "uncommon",    "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Severe Bleeding",  "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Seek emergency care for blood in urine, black/tarry stools, coughing blood, or severe headache — may indicate internal bleeding."},
    ],
    "apixaban": [
        {"display_name": "Easy Bruising",   "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Bleeding",        "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Some bleeding risk is expected. Does not require routine blood monitoring like warfarin."},
        {"display_name": "Nausea",          "frequency_category": "uncommon","severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Severe Bleeding", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Seek emergency care for unusual or uncontrolled bleeding — no reversal agent widely available outside hospital."},
    ],
    "rivaroxaban": [
        {"display_name": "Easy Bruising",   "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Bleeding",        "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Nausea",          "frequency_category": "uncommon","severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Severe Bleeding", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Seek emergency care for signs of major bleeding. Take with evening meal for better absorption."},
    ],
    "clopidogrel": [
        {"display_name": "Easy Bruising",   "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Bleeding",        "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Nausea",          "frequency_category": "uncommon", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",        "frequency_category": "uncommon", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Severe Bleeding", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Major bleeding risk. Do not stop without consulting your cardiologist — stopping suddenly can trigger heart attack."},
        {"display_name": "Thrombotic Thrombocytopenic Purpura", "frequency_category": "rare", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening blood disorder. Seek immediate care for fever, confusion, or unusual bruising."},
    ],
    # -----------------------------------------------------------------------
    # Diuretics
    # -----------------------------------------------------------------------
    "hydrochlorothiazide": [
        {"display_name": "Increased Urination", "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Diuretics cause more frequent urination. Take in the morning to avoid nighttime trips to the bathroom."},
        {"display_name": "Low Potassium",       "frequency_category": "common",      "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Can deplete potassium. Eat potassium-rich foods (bananas, oranges) or ask about supplements."},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dehydration",         "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Drink enough fluids. Avoid excessive sun and exercise in heat."},
        {"display_name": "Elevated Blood Sugar","frequency_category": "uncommon",    "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Gout",                "frequency_category": "uncommon",    "severity": "mild",     "management": "contact_doctor", "red_flag": False, "patient_description": "Thiazides can raise uric acid and trigger gout attacks."},
        {"display_name": "Severe Electrolyte Imbalance", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerously low sodium or potassium can cause muscle cramps, weakness, or heart arrhythmias. Seek care if you feel very weak or have an irregular heartbeat."},
    ],
    "furosemide": [
        {"display_name": "Increased Urination", "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Very powerful diuretic — causes significantly increased urination. Take in morning."},
        {"display_name": "Low Potassium",       "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Loop diuretics deplete potassium. Your doctor will likely monitor your electrolytes."},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dehydration",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Hearing Loss",        "frequency_category": "uncommon",    "severity": "moderate","management": "contact_doctor", "red_flag": False, "patient_description": "High doses or IV furosemide can damage hearing. Contact your doctor for ringing in the ears."},
        {"display_name": "Severe Electrolyte Imbalance", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause life-threatening low sodium, potassium, or magnesium. Regular lab monitoring is essential."},
    ],
    "spironolactone": [
        {"display_name": "Increased Urination",        "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "High Potassium",             "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Unlike other diuretics, spironolactone raises potassium. Avoid high-potassium foods if prescribed."},
        {"display_name": "Dizziness",                  "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Breast Tenderness",          "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Breast tenderness or swelling (gynecomastia) in men. Usually dose-related."},
        {"display_name": "Menstrual Irregularities",   "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Severe Hyperkalemia",        "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerously high potassium can cause fatal heart arrhythmias. Do not use potassium supplements without doctor approval."},
    ],
    # -----------------------------------------------------------------------
    # Respiratory
    # -----------------------------------------------------------------------
    "albuterol": [
        {"display_name": "Tremors",        "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Shaky hands or body tremors — very common and usually go away within 30 minutes."},
        {"display_name": "Palpitations",   "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Nervousness",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fast Heart Rate","frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Heart rate typically increases 15-20 beats per minute after use. Normal effect of the medication."},
        {"display_name": "Paradoxical Bronchospasm", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare — airway spasm worsens instead of improving. Stop using the inhaler and seek emergency care immediately."},
    ],
    "montelukast": [
        {"display_name": "Headache",     "frequency_category": "common",  "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",       "frequency_category": "common",  "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain", "frequency_category": "common",  "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Neuropsychiatric Effects", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA black box warning: can cause agitation, depression, sleep disorders, hallucinations, and suicidal thinking. Discuss risk/benefit with your doctor."},
    ],
    "fluticasone": [
        {"display_name": "Nosebleed",          "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Nasal bleeding is the most common side effect. Aim the spray away from the nasal septum."},
        {"display_name": "Nasal Irritation",   "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",           "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Throat Irritation",  "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Adrenal Suppression","frequency_category": "uncommon",  "severity": "mild", "management": "monitor",        "red_flag": False, "patient_description": "Relevant only with high doses or long-term use. Your doctor will monitor if needed."},
    ],
    # -----------------------------------------------------------------------
    # Antidepressants (additional SSRIs/SNRIs/other)
    # -----------------------------------------------------------------------
    "bupropion": [
        {"display_name": "Dry Mouth",            "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",             "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",               "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Insomnia",             "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Take bupropion earlier in the day to reduce sleep problems."},
        {"display_name": "Constipation",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Increased Blood Pressure", "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Tremors",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Seizures",             "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dose-dependent seizure risk. Never exceed prescribed dose. Contraindicated in eating disorders and alcohol withdrawal."},
        {"display_name": "Suicidal Thoughts",    "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Antidepressants can increase suicidal thinking in young adults. Monitor closely when starting treatment."},
    ],
    "venlafaxine": [
        {"display_name": "Nausea",               "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Most common when starting — take with food to help."},
        {"display_name": "Dry Mouth",            "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sweating",             "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Insomnia",             "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dizziness",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sexual Dysfunction",   "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Increased Blood Pressure", "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Can raise blood pressure, especially at higher doses. Regular BP monitoring is recommended."},
        {"display_name": "Serotonin Syndrome",   "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous drug interaction. Seek emergency care for agitation, rapid heart rate, or high body temperature."},
        {"display_name": "Suicidal Thoughts",    "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Monitor closely when starting. Do not stop abruptly — taper under doctor supervision to avoid withdrawal."},
    ],
    "duloxetine": [
        {"display_name": "Nausea",             "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce nausea when starting treatment."},
        {"display_name": "Dry Mouth",          "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Drowsiness",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",            "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Constipation",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Insomnia",           "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dizziness",          "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Sweating",           "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Sexual Dysfunction", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Serotonin Syndrome", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous drug interaction causing agitation, fever, and rapid heart rate. Seek emergency care immediately."},
        {"display_name": "Suicidal Thoughts",  "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Monitor closely when starting. Never stop abruptly — taper under doctor supervision."},
    ],
    "trazodone": [
        {"display_name": "Drowsiness",     "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Strong sedation — most people take trazodone at bedtime specifically for this effect."},
        {"display_name": "Dizziness",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dry Mouth",      "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Blurred Vision", "frequency_category": "uncommon",    "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Priapism",       "frequency_category": "rare",        "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Prolonged, painful erection not related to sexual stimulation. Seek emergency care within 4 hours to prevent permanent damage."},
    ],
    # -----------------------------------------------------------------------
    # Anxiolytics / buspirone
    # -----------------------------------------------------------------------
    "buspirone": [
        {"display_name": "Dizziness",       "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Feeling lightheaded — most common when starting. Usually improves after 1-2 weeks."},
        {"display_name": "Nausea",          "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce stomach upset."},
        {"display_name": "Headache",        "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nervousness",     "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False, "patient_description": "Paradoxical anxiety can occur early in treatment — this is normal and typically resolves within 2 weeks."},
        {"display_name": "Excitement",      "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Insomnia",        "frequency_category": "common",      "severity": "mild", "management": "monitor",        "red_flag": False},
        {"display_name": "Lightheadedness", "frequency_category": "common",      "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    # -----------------------------------------------------------------------
    # Benzodiazepines
    # -----------------------------------------------------------------------
    "alprazolam": [
        {"display_name": "Drowsiness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Do not drive or operate machinery. Sedation is dose-related."},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Memory Problems",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Coordination Issues", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Depression",          "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Physical dependence develops quickly. Never stop abruptly — seizures and severe withdrawal can occur. Taper under medical supervision."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Especially dangerous with opioids, alcohol, or other CNS depressants. Seek emergency care for slow or difficult breathing."},
    ],
    "clonazepam": [
        {"display_name": "Drowsiness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Memory Problems",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Coordination Issues", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Depression",          "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Long half-life but withdrawal can still be severe. Never stop abruptly — taper under medical supervision."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous when combined with opioids or alcohol. Seek emergency care for slow breathing."},
    ],
    "lorazepam": [
        {"display_name": "Drowsiness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Memory Problems",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Anterograde amnesia (inability to form new memories) is common, especially at higher doses."},
        {"display_name": "Coordination Issues", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Never stop abruptly — can cause life-threatening withdrawal seizures. Always taper under doctor supervision."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous with opioids, alcohol, or other CNS depressants. Emergency care needed for slow or stopped breathing."},
    ],
    "diazepam": [
        {"display_name": "Drowsiness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Muscle Weakness",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Memory Problems",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Fatigue",             "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Long-acting but withdrawal is still dangerous. Abrupt discontinuation can cause seizures. Always taper."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous with opioids or alcohol. Seek emergency care for breathing difficulty."},
    ],
    # -----------------------------------------------------------------------
    # Sleep aids
    # -----------------------------------------------------------------------
    "zolpidem": [
        {"display_name": "Drowsiness",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take only when you can get 7-8 hours of sleep. Do not drive if residual drowsiness occurs the next day."},
        {"display_name": "Dizziness",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Memory Loss",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "May not remember events that occurred after taking the medication — especially at higher doses."},
        {"display_name": "Complex Sleep Behaviors", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA warning: sleepwalking, sleep-driving, and other activities while asleep with no memory. Stop and contact your doctor immediately."},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Intended for short-term use only (2-4 weeks). Physical dependence and withdrawal can occur. Do not stop abruptly."},
    ],
    # -----------------------------------------------------------------------
    # Antipsychotics
    # -----------------------------------------------------------------------
    "quetiapine": [
        {"display_name": "Drowsiness",        "frequency_category": "very_common", "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Strong sedation is common — typically improves after a few weeks."},
        {"display_name": "Weight Gain",       "frequency_category": "very_common", "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Significant weight gain is common with quetiapine — diet and exercise monitoring is recommended."},
        {"display_name": "Dizziness",         "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dry Mouth",         "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation",      "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Elevated Blood Sugar", "frequency_category": "common",   "severity": "mild",     "management": "monitor",        "red_flag": False},
        {"display_name": "Metabolic Syndrome","frequency_category": "uncommon",    "severity": "moderate", "management": "monitor",        "red_flag": False},
        {"display_name": "Tardive Dyskinesia","frequency_category": "serious",     "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Involuntary movements of face, tongue, or limbs that may become permanent. Report any new repetitive movements to your doctor."},
        {"display_name": "Neuroleptic Malignant Syndrome", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening — high fever, rigid muscles, altered mental status. Seek emergency care immediately."},
    ],
    "risperidone": [
        {"display_name": "Weight Gain",          "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Drowsiness",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Extrapyramidal Symptoms", "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Stiffness, tremor, restlessness (akathisia), or slow movement. Tell your doctor if these occur."},
        {"display_name": "Elevated Prolactin",   "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Increased prolactin can cause breast changes or menstrual irregularities."},
        {"display_name": "Dizziness",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Tardive Dyskinesia",   "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Involuntary repetitive movements that may be irreversible. Report any new movements to your doctor immediately."},
        {"display_name": "Neuroleptic Malignant Syndrome", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening. High fever, rigid muscles, confusion — seek emergency care."},
    ],
    "aripiprazole": [
        {"display_name": "Nausea",            "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",          "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",         "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Akathisia (Restlessness)", "frequency_category": "common", "severity": "mild", "management": "monitor", "red_flag": False, "patient_description": "Inner restlessness, need to pace or move constantly. Tell your doctor — dose adjustment may help."},
        {"display_name": "Insomnia",          "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Weight Gain",       "frequency_category": "uncommon", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Tardive Dyskinesia","frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Involuntary repetitive movements. Report any new movement problems to your doctor."},
        {"display_name": "Impulse Control Issues", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA warning: compulsive gambling, hypersexuality, binge eating, or shopping. Tell your doctor if you notice any new urges."},
    ],
    "olanzapine": [
        {"display_name": "Weight Gain",       "frequency_category": "very_common", "severity": "moderate","management": "monitor",       "red_flag": False, "patient_description": "Significant weight gain is one of the most common side effects — diet monitoring is strongly recommended."},
        {"display_name": "Drowsiness",        "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dry Mouth",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation",      "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Elevated Blood Sugar","frequency_category": "common",    "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Can cause new or worsened diabetes. Monitor blood sugar regularly."},
        {"display_name": "Metabolic Syndrome","frequency_category": "common",      "severity": "moderate","management": "monitor",       "red_flag": False, "patient_description": "Combination of weight gain, high blood sugar, and high cholesterol — requires regular monitoring."},
        {"display_name": "Tardive Dyskinesia","frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Involuntary repetitive movements that can be permanent. Report any new movement changes immediately."},
        {"display_name": "Neuroleptic Malignant Syndrome", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "High fever, rigid muscles, confusion — seek emergency care immediately."},
    ],
    # -----------------------------------------------------------------------
    # ADHD (stimulants and non-stimulant)
    # -----------------------------------------------------------------------
    "methylphenidate": [
        {"display_name": "Decreased Appetite",   "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Very common — eating before taking medication can help. Appetite usually returns in the evening."},
        {"display_name": "Insomnia",             "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Avoid taking doses late in the day. Talk to your doctor if sleep problems persist."},
        {"display_name": "Headache",             "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce stomach discomfort."},
        {"display_name": "Increased Heart Rate", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Irritability",         "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Anxiety",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Weight Loss",          "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Growth Slowdown",      "frequency_category": "uncommon",    "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Children may have slightly slower growth with long-term use. Your doctor will monitor height and weight."},
        {"display_name": "Cardiovascular Events","frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Stimulants can increase heart rate and blood pressure. Do not use if you have structural heart defects. Seek care for chest pain or irregular heartbeat."},
    ],
    "amphetamine": [
        {"display_name": "Decreased Appetite",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Often significant — eat a nutritious meal before taking the medication."},
        {"display_name": "Insomnia",              "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Avoid afternoon doses. Good sleep hygiene is essential."},
        {"display_name": "Headache",              "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Increased Heart Rate",  "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dry Mouth",             "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Weight Loss",           "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Anxiety",               "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Irritability",          "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Cardiovascular Events", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can increase heart rate and blood pressure significantly. Do not use with pre-existing heart conditions without cardiology clearance."},
        {"display_name": "Dependence / Abuse Potential", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Schedule II controlled substance with high potential for abuse and dependence. Use only as prescribed."},
    ],
    "atomoxetine": [
        {"display_name": "Decreased Appetite",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",                "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce nausea."},
        {"display_name": "Insomnia",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Headache",              "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Increased Heart Rate",  "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Increased Blood Pressure", "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dry Mouth",             "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Suicidal Thoughts",     "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA black box warning for increased suicidal thinking in children and adolescents. Monitor closely when starting."},
        {"display_name": "Liver Toxicity",        "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but serious. Contact your doctor for yellow skin or eyes, dark urine, or severe abdominal pain."},
    ],
    # -----------------------------------------------------------------------
    # Alpha-2 agonists (ADHD / BP)
    # -----------------------------------------------------------------------
    "clonidine": [
        {"display_name": "Drowsiness",           "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Very sedating, especially at the start. Dose is often given at bedtime to take advantage of this effect."},
        {"display_name": "Dry Mouth",            "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Constipation",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Rebound Hypertension", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Sudden stopping causes severe rebound high blood pressure. Never stop abruptly — always taper under medical supervision."},
    ],
    "guanfacine": [
        {"display_name": "Drowsiness",           "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Less sedating than clonidine, but still causes sleepiness. Often given at bedtime."},
        {"display_name": "Fatigue",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dizziness",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",             "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dry Mouth",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Rebound Hypertension", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Stopping abruptly can cause rebound high blood pressure. Always taper with doctor guidance."},
    ],
    # -----------------------------------------------------------------------
    # GI (additional)
    # -----------------------------------------------------------------------
    "pantoprazole": [
        {"display_name": "Headache",             "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",             "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",               "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain",         "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Magnesium Deficiency", "frequency_category": "uncommon", "severity": "mild", "management": "monitor",        "red_flag": False, "patient_description": "Long-term PPI use may lower magnesium. Symptoms include muscle cramps and irregular heartbeat."},
        {"display_name": "C. diff Infection",    "frequency_category": "uncommon", "severity": "moderate", "management": "contact_doctor", "red_flag": False},
    ],
    "famotidine": [
        {"display_name": "Headache",    "frequency_category": "common",   "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",   "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation","frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",    "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False},
    ],
    # -----------------------------------------------------------------------
    # Antibiotics (additional)
    # -----------------------------------------------------------------------
    "azithromycin": [
        {"display_name": "Diarrhea",     "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Pain", "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",     "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "QT Prolongation", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can prolong the QT interval and cause dangerous heart arrhythmias, especially in people with heart disease. Report palpitations or fainting."},
    ],
    "ciprofloxacin": [
        {"display_name": "Nausea",        "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",      "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",      "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",     "frequency_category": "common",   "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "C. diff Infection", "frequency_category": "uncommon", "severity": "moderate", "management": "contact_doctor", "red_flag": False},
        {"display_name": "Tendon Rupture","frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA black box warning: fluoroquinolones can cause tendon rupture, especially the Achilles tendon. Stop immediately for tendon pain or swelling."},
        {"display_name": "QT Prolongation","frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause dangerous heart rhythm abnormalities. Report palpitations or fainting."},
    ],
    "doxycycline": [
        {"display_name": "Nausea",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food or milk to reduce nausea, but avoid dairy within 2 hours as it reduces absorption."},
        {"display_name": "Photosensitivity","frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Skin sunburns easily — use SPF 30+ sunscreen and avoid prolonged sun exposure."},
        {"display_name": "Stomach Upset",   "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Diarrhea",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Esophageal Irritation", "frequency_category": "uncommon", "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Always take with a full glass of water and stay upright for 30 minutes to prevent esophageal ulcers."},
        {"display_name": "Yeast Infection", "frequency_category": "uncommon",    "severity": "mild",   "management": "manage_at_home", "red_flag": False},
    ],
    # -----------------------------------------------------------------------
    # Diabetes (additional)
    # -----------------------------------------------------------------------
    "glipizide": [
        {"display_name": "Low Blood Sugar (Hypoglycemia)", "frequency_category": "very_common", "severity": "mild", "management": "manage_at_home", "red_flag": False, "patient_description": "Symptoms: shakiness, sweating, confusion, fast heartbeat. Eat a small snack or drink juice. Always carry glucose tablets."},
        {"display_name": "Weight Gain",    "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Nausea",         "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",      "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Severe Hypoglycemia", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Severe low blood sugar can cause seizures, unconsciousness, or death. Seek emergency care if unable to eat or drink to correct."},
    ],
    "empagliflozin": [
        {"display_name": "Urinary Tract Infections", "frequency_category": "very_common", "severity": "mild",     "management": "contact_doctor", "red_flag": False, "patient_description": "More frequent UTIs. Contact your doctor for burning with urination or frequent urge to urinate."},
        {"display_name": "Genital Yeast Infections", "frequency_category": "very_common", "severity": "mild",     "management": "contact_doctor", "red_flag": False, "patient_description": "Common due to increased glucose in urine. Good hygiene and antifungal treatment help."},
        {"display_name": "Increased Urination",      "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dehydration",              "frequency_category": "common",      "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Drink enough fluids. Avoid excessive heat and heavy exercise without hydration."},
        {"display_name": "Diabetic Ketoacidosis",    "frequency_category": "serious",     "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can occur even with near-normal blood sugars. Seek emergency care for nausea, vomiting, belly pain, or difficulty breathing."},
        {"display_name": "Fournier's Gangrene",      "frequency_category": "rare",        "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening genital infection. Seek emergency care for pain, swelling, or redness around genitals/perineum."},
    ],
    # -----------------------------------------------------------------------
    # Corticosteroids
    # -----------------------------------------------------------------------
    "prednisone": [
        {"display_name": "Increased Appetite / Weight Gain", "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Very common — a healthy diet with limited salt and simple carbs can help manage this."},
        {"display_name": "Mood Changes",        "frequency_category": "common",   "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Can cause irritability, anxiety, or euphoria. Mood typically returns to normal after stopping."},
        {"display_name": "Insomnia",            "frequency_category": "common",   "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Take prednisone in the morning to reduce sleep disruption."},
        {"display_name": "Elevated Blood Sugar","frequency_category": "common",   "severity": "mild",     "management": "monitor",        "red_flag": False, "patient_description": "Can cause steroid-induced diabetes. Diabetics must monitor blood sugar closely."},
        {"display_name": "Fluid Retention",     "frequency_category": "common",   "severity": "mild",     "management": "manage_at_home", "red_flag": False},
        {"display_name": "Stomach Upset",       "frequency_category": "common",   "severity": "mild",     "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce stomach irritation."},
        {"display_name": "Increased Infection Risk", "frequency_category": "common", "severity": "moderate", "management": "monitor",     "red_flag": False, "patient_description": "Steroids suppress the immune system. Avoid people with active infections."},
        {"display_name": "Bone Loss (Osteoporosis)", "frequency_category": "uncommon", "severity": "moderate", "management": "monitor",   "red_flag": False, "patient_description": "Long-term use can weaken bones. Calcium and vitamin D supplementation may be recommended."},
        {"display_name": "Adrenal Suppression", "frequency_category": "serious",  "severity": "severe",   "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Stopping suddenly after long-term use can cause adrenal crisis. Never stop abruptly — always taper under doctor guidance."},
    ],
    "methylprednisolone": [
        {"display_name": "Increased Appetite",  "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Mood Changes",        "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Insomnia",            "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Fluid Retention",     "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Elevated Blood Sugar","frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Stomach Upset",       "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Adrenal Suppression", "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Never stop abruptly after prolonged use. Tapering is required to allow adrenal glands to recover."},
    ],
    # -----------------------------------------------------------------------
    # Anticonvulsants / mood stabilizers (additional)
    # -----------------------------------------------------------------------
    "pregabalin": [
        {"display_name": "Drowsiness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Do not drive until you know how pregabalin affects you."},
        {"display_name": "Dizziness",           "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Weight Gain",         "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dry Mouth",           "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Blurred Vision",      "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Swelling (Edema)",    "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Coordination Problems", "frequency_category": "common",    "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dependence / Withdrawal", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause physical dependence. Never stop abruptly — taper under doctor supervision to avoid withdrawal seizures."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Especially dangerous when combined with opioids or CNS depressants."},
    ],
    "topiramate": [
        {"display_name": "Cognitive Impairment", "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Word-finding difficulties, memory problems, and slowed thinking — often called 'dopamax' effect. Usually dose-related."},
        {"display_name": "Dizziness",            "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Weight Loss",          "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Often causes significant weight loss — can be a benefit or concern depending on the patient."},
        {"display_name": "Fatigue",              "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Paresthesia (Tingling)","frequency_category": "common",     "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Tingling or pins-and-needles in hands or feet — very common and usually harmless."},
        {"display_name": "Kidney Stones",        "frequency_category": "uncommon",    "severity": "moderate","management": "contact_doctor", "red_flag": False, "patient_description": "Drink plenty of water to reduce kidney stone risk."},
        {"display_name": "Metabolic Acidosis",   "frequency_category": "uncommon",    "severity": "moderate","management": "contact_doctor", "red_flag": False},
        {"display_name": "Acute Angle-Closure Glaucoma", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but serious — acute eye pain, decreased vision. Seek immediate eye care. Stop topiramate immediately."},
    ],
    "valproate": [
        {"display_name": "Weight Gain",   "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Tremors",       "frequency_category": "very_common", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Fine hand tremors are common — tell your doctor if they interfere with daily activities."},
        {"display_name": "Nausea",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food. Extended-release formulations may cause less GI upset."},
        {"display_name": "Hair Loss",     "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Hair thinning or loss is common. Often improves with time or after adding zinc/selenium supplements."},
        {"display_name": "Drowsiness",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Liver Toxicity","frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Most dangerous in children under 2. Seek care for yellow skin/eyes, abdominal pain, or loss of seizure control. Regular liver tests are essential."},
        {"display_name": "Neural Tube Defects", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA black box warning: causes major birth defects (spina bifida) if taken during pregnancy. Discuss with your doctor before any pregnancy."},
        {"display_name": "Pancreatitis",  "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Rare but life-threatening inflammation of the pancreas. Seek care for severe abdominal pain that radiates to the back."},
    ],
    "lamotrigine": [
        {"display_name": "Headache",    "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",   "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",      "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Blurred Vision","frequency_category": "common", "severity": "mild",  "management": "monitor",        "red_flag": False},
        {"display_name": "Insomnia",    "frequency_category": "common",  "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Stevens-Johnson Syndrome", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Life-threatening skin reaction — especially with rapid dose increases. Stop immediately and seek emergency care for rash, blistering, or peeling skin."},
        {"display_name": "Suicidal Thoughts", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "All anticonvulsants carry an FDA warning for increased suicidal thinking. Monitor closely."},
    ],
    # -----------------------------------------------------------------------
    # Opioids / pain
    # -----------------------------------------------------------------------
    "tramadol": [
        {"display_name": "Nausea",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food to reduce nausea. Anti-nausea medication can be prescribed if needed."},
        {"display_name": "Dizziness",   "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation","frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Drowsiness",  "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Seizures",    "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Tramadol lowers seizure threshold, especially at high doses or with other seizure risk factors. Seek emergency care for any seizure."},
        {"display_name": "Serotonin Syndrome", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous interaction with antidepressants. Seek emergency care for agitation, rapid heart rate, or high temperature."},
        {"display_name": "Dependence / Respiratory Depression", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Schedule IV opioid — can cause dependence and respiratory depression. Never combine with alcohol, benzodiazepines, or other CNS depressants."},
    ],
    "hydrocodone": [
        {"display_name": "Constipation",  "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Opioid-induced constipation is nearly universal. Stool softeners or laxatives are usually recommended."},
        {"display_name": "Drowsiness",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Do not drive or operate machinery. Avoid alcohol."},
        {"display_name": "Nausea",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",     "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",      "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dependence / Addiction", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Schedule II opioid with high potential for abuse and physical dependence. Use only as prescribed and for the shortest duration possible."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause fatal slowing of breathing — especially dangerous with alcohol, benzodiazepines, or other CNS depressants. Seek emergency care for slow or stopped breathing."},
    ],
    "oxycodone": [
        {"display_name": "Constipation",  "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Drowsiness",    "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Do not drive or operate heavy machinery. Effects intensified by alcohol."},
        {"display_name": "Nausea",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",     "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",      "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dependence / Addiction", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Schedule II — high potential for abuse and addiction. Extended-release formulations must never be crushed or chewed."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Potentially fatal breathing suppression — especially with alcohol, benzodiazepines, or other CNS depressants. Have naloxone (Narcan) available."},
    ],
    "morphine": [
        {"display_name": "Constipation",      "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Bowel regimen (stool softeners + laxatives) is almost always needed with regular morphine use."},
        {"display_name": "Drowsiness",        "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",            "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Vomiting",          "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Urinary Retention", "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Difficulty urinating — common with opioids. Contact your doctor if you cannot urinate."},
        {"display_name": "Dependence / Addiction", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "High potential for dependence and addiction. Use only under close medical supervision for the shortest time needed."},
        {"display_name": "Respiratory Depression", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause fatal respiratory arrest — especially with alcohol or CNS depressants. Have naloxone (Narcan) immediately available."},
    ],
    # -----------------------------------------------------------------------
    # Other / miscellaneous
    # -----------------------------------------------------------------------
    "ondansetron": [
        {"display_name": "Headache",     "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Constipation", "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Fatigue",      "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Dizziness",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "QT Prolongation", "frequency_category": "serious",  "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "IV ondansetron at high doses can prolong QT interval. Tell your doctor if you have a known QT problem or take other QT-prolonging drugs."},
    ],
    "cyclobenzaprine": [
        {"display_name": "Drowsiness",  "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Very common — do not drive or operate machinery. Avoid alcohol."},
        {"display_name": "Dry Mouth",   "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",   "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",    "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",      "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Serotonin Syndrome", "frequency_category": "rare", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Dangerous interaction with antidepressants, tramadol, or other serotonergic drugs. Seek emergency care for agitation, fever, or rapid heart rate."},
    ],
    "methotrexate": [
        {"display_name": "Nausea",              "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take on the night before if possible, or split the dose. Folic acid supplementation significantly reduces nausea."},
        {"display_name": "Fatigue",             "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Mouth Sores",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Folic acid supplementation (as prescribed) reduces mouth sores."},
        {"display_name": "Hair Thinning",       "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Loss of Appetite",    "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Liver Toxicity",      "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause progressive liver damage with long-term use. Regular liver function tests and periodic liver biopsies may be needed."},
        {"display_name": "Pulmonary Toxicity",  "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can cause interstitial pneumonitis. Seek care for progressive shortness of breath or persistent cough."},
        {"display_name": "Bone Marrow Suppression", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can suppress blood cell production causing infection risk and bleeding. Regular blood count monitoring is essential."},
    ],
    "hydroxychloroquine": [
        {"display_name": "Nausea",               "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Take with food or milk to reduce stomach upset."},
        {"display_name": "Stomach Upset",        "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Headache",             "frequency_category": "common",  "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Skin Pigmentation Changes", "frequency_category": "uncommon", "severity": "mild", "management": "monitor", "red_flag": False},
        {"display_name": "Retinal Toxicity",     "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can permanently damage the retina with long-term use. Regular eye exams (annually after 5 years of use) are essential to detect early changes."},
        {"display_name": "QT Prolongation",      "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Can trigger dangerous heart arrhythmias in susceptible individuals. Tell your doctor about any heart rhythm problems."},
    ],
    "adalimumab": [
        {"display_name": "Injection Site Reactions", "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Redness, swelling, or pain at injection site — very common. Rotate injection sites and apply a cold pack before injecting."},
        {"display_name": "Headache",               "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Nausea",                 "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Upper Respiratory Infections", "frequency_category": "common", "severity": "mild",  "management": "monitor",        "red_flag": False, "patient_description": "More frequent colds and respiratory infections due to immune suppression."},
        {"display_name": "Serious Infections",     "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "FDA black box warning: can reactivate tuberculosis and cause serious fungal or bacterial infections. Get TB tested before starting. Seek care for fever, chills, persistent cough."},
        {"display_name": "Malignancy Risk",        "frequency_category": "serious",     "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "TNF inhibitors are associated with slightly increased risk of lymphoma. Discuss risk/benefit with your doctor."},
    ],
    "tamsulosin": [
        {"display_name": "Dizziness",          "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Especially on standing up from sitting/lying down (orthostatic hypotension). Rise slowly."},
        {"display_name": "Ejaculation Problems", "frequency_category": "very_common", "severity": "mild",  "management": "monitor",       "red_flag": False, "patient_description": "Retrograde ejaculation (dry orgasm) is very common — harmless but worth discussing with your doctor."},
        {"display_name": "Runny Nose",         "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Orthostatic Hypotension", "frequency_category": "common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Intraoperative Floppy Iris Syndrome", "frequency_category": "uncommon", "severity": "moderate", "management": "contact_doctor", "red_flag": False, "patient_description": "Tell your eye surgeon you take tamsulosin BEFORE any cataract surgery — it can complicate the procedure."},
    ],
    "sildenafil": [
        {"display_name": "Headache",         "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Flushing",         "frequency_category": "very_common", "severity": "mild",   "management": "manage_at_home", "red_flag": False, "patient_description": "Warmth and redness of the face and neck — common and temporary."},
        {"display_name": "Nasal Congestion", "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Dizziness",        "frequency_category": "common",      "severity": "mild",   "management": "manage_at_home", "red_flag": False},
        {"display_name": "Blurred Vision",   "frequency_category": "common",      "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Severe Hypotension (with Nitrates)", "frequency_category": "serious", "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "NEVER combine with nitrates (nitroglycerin, isosorbide) — can cause life-threatening drop in blood pressure. This includes recreational 'poppers'."},
        {"display_name": "Priapism",         "frequency_category": "rare",        "severity": "severe", "management": "contact_doctor", "red_flag": True, "red_flag_reason": "Prolonged erection (>4 hours) is a medical emergency. Seek immediate emergency care to prevent permanent damage."},
    ],
    "finasteride": [
        {"display_name": "Decreased Sex Drive",   "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Loss of libido — affects up to 3-4% of users. Usually reversible after stopping."},
        {"display_name": "Erectile Dysfunction",  "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Ejaculation Disorder",  "frequency_category": "common",   "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Breast Tenderness",     "frequency_category": "uncommon", "severity": "mild",   "management": "monitor",        "red_flag": False},
        {"display_name": "Depression",            "frequency_category": "uncommon", "severity": "mild",   "management": "monitor",        "red_flag": False, "patient_description": "Report mood changes, especially depression, to your doctor."},
        {"display_name": "Prostate Cancer Risk Masking", "frequency_category": "serious", "severity": "moderate", "management": "contact_doctor", "red_flag": False, "patient_description": "Finasteride lowers PSA levels — inform your doctor so PSA results are interpreted correctly for prostate cancer screening."},
    ],
}


def _get_class_fallback(drug_name: str) -> dict | None:
    """
    Return hardcoded drug-class fallback data for known drugs.
    Returns None if the drug is not in the fallback catalog.
    """
    key = drug_name.strip().lower()
    resolved = _DRUG_ALIASES.get(key, key)
    effects = _DRUG_CLASS_FALLBACKS.get(resolved)
    if not effects:
        return None

    tiers: dict = {
        "very_common": {"label": "Very Common (>10%)",  "items": []},
        "common":      {"label": "Common (1–10%)",       "items": []},
        "uncommon":    {"label": "Uncommon (0.1–1%)",    "items": []},
        "rare":        {"label": "Rare (<0.1%)",          "items": []},
        "serious":     {"label": "Serious — Seek Medical Attention", "items": [], "urgent": True},
    }
    for item in effects:
        freq = item.get("frequency_category", "common")
        if freq not in tiers:
            freq = "common"
        clean = _validate_effect_schema(item)
        tiers[freq]["items"].append(clean)

    has_red = any(item.get("red_flag") for item in effects)
    logger.info("[SEStore] Class fallback for %s (%s): %d effects",
                drug_name, resolved, sum(len(t["items"]) for t in tiers.values()))

    return {
        "drug":           resolved,
        "generic_name":   resolved,
        "brand_names":    [],
        "side_effects":   tiers,
        "boxed_warnings": [],
        "mechanism_of_action": {
            "summary": "", "detail": "",
            "pharmacologic_class": "", "molecular_targets": [],
        },
        "sources": [{
            "id": 1, "name": "RxBuddy Clinical Reference (FDA-sourced)",
            "url": f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{resolved}"&limit=1',
            "section": "Known Side Effects", "last_updated": "",
        }],
        "overall_confidence": 0.6,
        "overall_verdict":    "CONSULT_PHARMACIST" if has_red else "CAUTION",
        "has_red_flag":       has_red,
        "_fallback":          True,
        "_fallback_source":   "drug_class_hardcoded",
    }
