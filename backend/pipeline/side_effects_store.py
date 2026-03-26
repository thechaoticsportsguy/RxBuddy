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
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
    except Exception as exc:
        logger.error("[SEStore] Gemini init failed: %s", exc)
        return None

    # Build label context
    section_texts = []
    for sec in ("adverse_reactions", "warnings", "boxed_warning",
                "warnings_and_precautions", "clinical_pharmacology", "description"):
        txt = (fda_label or {}).get(sec, "")
        if txt:
            section_texts.append(f"=== {sec.upper()} ===\n{txt[:1200]}")

    if not section_texts:
        logger.warning("[SEStore] No label sections for %s — cannot parse", drug_name)
        return None

    prompt = _build_gemini_prompt(drug_name, "\n\n".join(section_texts))

    t0 = time.time()
    try:
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 2000},
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

    # Strip markdown fences
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```")[0].strip()
    elif raw.startswith("```"):
        raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```")).strip()

    try:
        effects_list = json.loads(raw)
        if not isinstance(effects_list, list):
            raise ValueError("Response is not a JSON array")
        if not effects_list:
            raise ValueError("Response array is empty")
    except Exception as exc:
        logger.error("[SEStore] Gemini JSON parse failed for %s: %s — raw: %.200s",
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

    Priority:
      1. DB cache            — < 10ms
      2. Gemini parse + store — first hit only
      3. None                — caller falls back to heuristic parser

    Always logs: cache hit/miss, Gemini latency, fallback usage.
    Never returns empty results — returns None to trigger caller fallback.
    """
    if not drug_name:
        return None

    # 1. DB lookup
    t0 = time.time()
    cached = get_from_db(drug_name)
    if cached:
        logger.info("[SEStore] Cache HIT for %s (%.0fms)", drug_name, (time.time()-t0)*1000)
        return cached

    logger.info("[SEStore] Cache MISS for %s", drug_name)

    if not fda_label:
        logger.warning("[SEStore] No FDA label for %s — skipping Gemini, trying class fallback", drug_name)
        # Step 4: class hardcoded fallback
        fallback = _get_class_fallback(drug_name)
        if fallback:
            return fallback
        logger.warning("[SEStore] No fallback available for %s", drug_name)
        return None

    # 2. Gemini parse
    parsed = parse_label_with_gemini(drug_name, fda_label)

    if not parsed:
        logger.warning("[SEStore] Gemini failed for %s — trying heuristic label parse", drug_name)

        # 3. Heuristic regex parse of the FDA label (no AI)
        heuristic = _quick_heuristic_parse(drug_name, fda_label)
        if heuristic:
            return heuristic

        # 4. Drug-class hardcoded fallback
        fallback = _get_class_fallback(drug_name)
        if fallback:
            return fallback

        logger.warning("[SEStore] All parsers failed for %s — no data available", drug_name)
        return None

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

    # 3. Store to DB
    try:
        store_to_db(drug_name, parsed)
    except Exception as exc:
        logger.warning("[SEStore] Store failed for %s: %s", drug_name, exc)

    return parsed


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
    "tylenol":   "acetaminophen",
    "advil":     "ibuprofen",
    "motrin":    "ibuprofen",
    "aleve":     "naproxen",
    "lipitor":   "atorvastatin",
    "crestor":   "rosuvastatin",
    "zoloft":    "sertraline",
    "prozac":    "fluoxetine",
    "lexapro":   "escitalopram",
    "glucophage": "metformin",
    "prinivil":  "lisinopril",
    "zestril":   "lisinopril",
    "norvasc":   "amlodipine",
    "nexium":    "esomeprazole",
    "prilosec":  "omeprazole",
    "synthroid": "levothyroxine",
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
