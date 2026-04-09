"""Async FDA, RxNorm, MedlinePlus and RxImage HTTP clients (httpx)."""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from exceptions import FDAUnavailable

logger = logging.getLogger("rxbuddy.fda")

# ── URLs ─────────────────────────────────────────────────────────────────────
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
RXNORM_DISPLAYNAMES_URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/displaynames.json"
RXNORM_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
MEDLINEPLUS_CONNECT_URL = "https://connect.medlineplus.gov/application"
RXIMAGE_API_URL = "https://rximage.nlm.nih.gov/api/rximage/1/rxbase"

_HEADERS = {"User-Agent": "RxBuddy/1.0"}

# ── Shared httpx client (created lazily, reused across calls) ────────────────
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0)
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ── Brand → Generic mapping ─────────────────────────────────────────────────
BRAND_TO_GENERIC: dict[str, str] = {
    "tylenol": "acetaminophen", "advil": "ibuprofen", "motrin": "ibuprofen",
    "aleve": "naproxen", "bayer": "aspirin", "excedrin": "acetaminophen",
    "benadryl": "diphenhydramine", "claritin": "loratadine", "zyrtec": "cetirizine",
    "allegra": "fexofenadine", "prilosec": "omeprazole", "nexium": "esomeprazole",
    "pepcid": "famotidine", "xanax": "alprazolam", "valium": "diazepam",
    "ambien": "zolpidem", "zoloft": "sertraline", "prozac": "fluoxetine",
    "lexapro": "escitalopram", "lipitor": "atorvastatin", "crestor": "rosuvastatin",
    "viagra": "sildenafil", "cialis": "tadalafil", "synthroid": "levothyroxine",
}


# ── Drug name extraction ────────────────────────────────────────────────────

def extract_drug_name(question: str) -> str | None:
    """Extract the primary drug name from a question (brand → generic)."""
    q_lower = question.lower()
    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower:
            return generic
    _COMMON = [
        "acetaminophen", "ibuprofen", "aspirin", "naproxen", "amoxicillin",
        "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
        "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
        "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
        "hydrocodone", "oxycodone", "tramadol", "warfarin", "ciprofloxacin",
    ]
    for drug in _COMMON:
        if drug in q_lower:
            return drug
    return None


def extract_drug_names(question: str) -> list[str]:
    """
    Extract ALL drug names from a question.

    Resolution order:
      1. BRAND_TO_GENERIC map
      2. Full drug_catalog (brand + canonical)
      3. services.drug_resolver (Levenshtein + RxNorm)
    """
    q_lower = question.lower()
    found: list[str] = []

    for brand, generic in BRAND_TO_GENERIC.items():
        if brand in q_lower and generic not in found:
            found.append(generic)

    try:
        from drug_catalog import _CATALOG, _ALIAS_MAP

        for canonical_key in _CATALOG:
            if canonical_key in q_lower and canonical_key not in found:
                found.append(canonical_key)
        for alias, canonical_key in _ALIAS_MAP.items():
            if alias in q_lower and canonical_key not in found:
                found.append(canonical_key)
    except Exception:
        pass

    if not found:
        try:
            from services.drug_resolver import extract_generic_names

            for g in extract_generic_names(question):
                if g not in found:
                    found.append(g)
        except Exception:
            pass

    return found


# ── OpenFDA label search ────────────────────────────────────────────────────

async def openfda_search(field: str, value: str) -> tuple[dict | None, dict | None]:
    """Single OpenFDA label search. Returns (parsed_fda_data, raw_label) or (None, None)."""
    client = _get_client()
    try:
        url = f'{OPENFDA_LABEL_URL}?search=openfda.{field}:"{value}"&limit=1'
        resp = await client.get(url)
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None, None

        raw_label = results[0]

        def get_section(k: str) -> str:
            val = raw_label.get(k, [])
            return val[0][:1200] if isinstance(val, list) and val else ""

        fda_data = {
            "drug_name":                   value,
            "warnings":                    get_section("warnings"),
            "boxed_warning":               get_section("boxed_warning"),
            "dosage_and_administration":    get_section("dosage_and_administration"),
            "contraindications":           get_section("contraindications"),
            "drug_interactions":           get_section("drug_interactions"),
            "adverse_reactions":           get_section("adverse_reactions"),
            "pregnancy":                   get_section("pregnancy"),
            "lactation":                   get_section("lactation"),
            "indications_and_usage":       get_section("indications_and_usage"),
            "use_in_specific_populations": get_section("use_in_specific_populations"),
        }
        has_data = any(v for k, v in fda_data.items() if k != "drug_name" and v)
        return (fda_data, raw_label) if has_data else (None, None)

    except httpx.HTTPStatusError as exc:
        logger.debug("[FDA] openfda_search %s=%s HTTP %s", field, value, exc.response.status_code)
        return None, None
    except Exception as exc:
        logger.debug("[FDA] openfda_search %s=%s failed: %s", field, value, exc)
        return None, None


async def fetch_fda_label(drug_name: str) -> dict | None:
    """Fetch parsed FDA label sections (no raw label)."""
    fda_data, _ = await fetch_fda_label_with_raw(drug_name)
    return fda_data


async def fetch_fda_label_with_raw(drug_name: str) -> tuple[dict | None, dict | None]:
    """
    Fetch FDA label with multiple fallback strategies.

    Search order:
      1. label_updater cache (24-h TTL)
      2. openfda.generic_name
      3. openfda.brand_name
      4. openfda.substance_name
      5. Catalog canonical + brand names
    """
    if not drug_name:
        return None, None

    key = drug_name.strip().lower()

    try:
        import label_updater

        cached = label_updater.get_cached_label(key)
        if cached is not None:
            logger.debug("[FDA] Cache HIT for '%s'", key)
            return cached.data, cached.raw_label
    except Exception:
        pass

    logger.info("[FDA] Cache MISS — fetching label for '%s'", key)

    search_attempts: list[tuple[str, str]] = [
        ("generic_name", drug_name),
        ("brand_name", drug_name),
        ("substance_name", drug_name),
    ]

    try:
        from drug_catalog import find_drug

        rec = find_drug(drug_name)
        if rec:
            canonical = rec.canonical_name
            if canonical.lower() != key:
                search_attempts.append(("generic_name", canonical))
            for brand in rec.brand_names[:3]:
                if brand.lower() != key:
                    search_attempts.append(("brand_name", brand))
    except Exception:
        pass

    fda_data, raw_label = None, None
    for field, value in search_attempts:
        fda_data, raw_label = await openfda_search(field, value)
        if fda_data:
            logger.info("[FDA] Resolved '%s' via openfda.%s=%s", drug_name, field, value)
            break

    if not fda_data:
        logger.info("[FDA] No label found for '%s' after all strategies", drug_name)
        return None, None

    try:
        import label_updater
        from answer_engine import extract_fda_metadata

        meta = extract_fda_metadata(raw_label)
        label_updater.put_label(
            drug_name=key,
            data=fda_data,
            raw_label=raw_label,
            label_revision_date=meta.get("label_revision_date"),
        )
    except Exception:
        pass

    return fda_data, raw_label


async def fetch_all_labels(drug_names: list[str]) -> dict[str, tuple[dict, dict]]:
    """Fetch FDA labels for every drug concurrently."""
    results: dict[str, tuple[dict, dict]] = {}

    async def _fetch_one(name: str) -> None:
        data, raw = await fetch_fda_label_with_raw(name)
        if data and raw:
            results[name] = (data, raw)

    await asyncio.gather(*[_fetch_one(n) for n in drug_names], return_exceptions=True)
    return results


# ── Cross-reference interactions ─────────────────────────────────────────────

def cross_reference_interactions(labels: dict[str, tuple[dict, dict]]) -> list[str]:
    """Scan each drug's interaction section for mentions of other drugs."""
    signals: list[str] = []
    drug_names = list(labels.keys())
    for source_drug, (fda_data, _) in labels.items():
        interactions_text = fda_data.get("drug_interactions", "").lower()
        if not interactions_text:
            continue
        for target_drug in drug_names:
            if target_drug == source_drug:
                continue
            search_terms = [target_drug]
            try:
                from drug_catalog import find_drug

                rec = find_drug(target_drug)
                if rec:
                    search_terms.append(rec.canonical_name)
                    search_terms.extend(rec.brand_names[:2])
            except Exception:
                pass

            for term in search_terms:
                idx = interactions_text.find(term.lower())
                if idx != -1:
                    start = max(0, idx - 60)
                    end = min(len(interactions_text), idx + 200)
                    snippet = fda_data["drug_interactions"][start:end].strip()
                    signals.append(
                        f"[FDA INTERACTION SIGNAL] {source_drug.upper()} label "
                        f"mentions '{term}': \"...{snippet}...\""
                    )
                    break
    return signals


# ── Context builders ─────────────────────────────────────────────────────────

def build_multi_drug_context(
    labels: dict[str, tuple[dict, dict]],
    question: str,
    medlineplus_data: dict | None = None,
) -> str:
    """Build grounded FDA context string for multi-drug queries."""
    if not labels and not medlineplus_data:
        return ""

    q_lower = question.lower()
    parts: list[str] = []

    for drug_name, (fda_data, _) in labels.items():
        drug_parts: list[str] = [f"━━━ FDA LABEL: {drug_name.upper()} ━━━"]
        if fda_data.get("boxed_warning"):
            drug_parts.append(f"BOXED WARNING: {fda_data['boxed_warning'][:600]}")
        if fda_data.get("drug_interactions"):
            drug_parts.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:700]}")
        if fda_data.get("warnings"):
            drug_parts.append(f"WARNINGS: {fda_data['warnings'][:500]}")
        if fda_data.get("contraindications"):
            drug_parts.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:400]}")
        if any(w in q_lower for w in ("pregnant", "pregnancy", "breastfeed", "nursing", "lactation")):
            for section in ("pregnancy", "lactation", "use_in_specific_populations"):
                if fda_data.get(section):
                    drug_parts.append(f"{section.upper()}: {fda_data[section][:400]}")
        if len(drug_parts) > 1:
            parts.append("\n".join(drug_parts))

    signals = cross_reference_interactions(labels)
    if signals:
        parts.append("━━━ CROSS-REFERENCE SIGNALS (extracted from FDA labels) ━━━")
        parts.extend(signals)

    if medlineplus_data:
        ml_parts = ["━━━ MEDLINEPLUS (plain-English supplement only) ━━━"]
        if medlineplus_data.get("summary"):
            ml_parts.append(f"SUMMARY: {medlineplus_data['summary'][:400]}")
        if medlineplus_data.get("side_effects"):
            ml_parts.append(f"SIDE EFFECTS: {medlineplus_data['side_effects'][:300]}")
        parts.append("\n".join(ml_parts))

    return "\n\n".join(parts)


def build_fda_context(
    fda_data: dict | None,
    question: str,
    medlineplus_data: dict | None = None,
    intent_str: str = "",
) -> str:
    """Build grounded context for single-drug queries (intent-aware)."""
    if not fda_data and not medlineplus_data:
        return ""

    q_lower = question.lower()
    fda_sections: list[str] = []
    medline_sections: list[str] = []
    drug_name = (fda_data or {}).get("drug_name", "Unknown")

    if fda_data:
        fda_sections.append("FDA CLINICAL DATA")
        fda_sections.append(f"Drug: {drug_name}")

        if fda_data.get("indications_and_usage"):
            fda_sections.append(f"INDICATIONS: {fda_data['indications_and_usage'][:400]}")
        if any(w in q_lower for w in ["dose", "dosage", "how much", "how many", "take"]):
            if fda_data.get("dosage_and_administration"):
                fda_sections.append(f"DOSAGE AND ADMINISTRATION: {fda_data['dosage_and_administration'][:500]}")
        if any(w in q_lower for w in ["warning", "danger", "risk", "safe", "unsafe"]):
            if fda_data.get("warnings"):
                fda_sections.append(f"WARNINGS: {fda_data['warnings'][:500]}")
        if any(w in q_lower for w in ["interact", "interaction", "with", "mix", "combine", "together"]):
            _no_int = frozenset({"side_effects", "what_is", "dosing", "pregnancy_lactation"})
            if intent_str not in _no_int and fda_data.get("drug_interactions"):
                fda_sections.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:500]}")
        if any(w in q_lower for w in ["shouldn't", "should not", "can't", "cannot", "avoid", "contraindication"]):
            if fda_data.get("contraindications"):
                fda_sections.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:500]}")
        if any(w in q_lower for w in ["pregnant", "pregnancy", "breastfeed", "nursing"]):
            if fda_data.get("pregnancy"):
                fda_sections.append(f"PREGNANCY: {fda_data['pregnancy'][:500]}")
        if any(w in q_lower for w in ["side effect", "reaction", "adverse"]):
            if fda_data.get("adverse_reactions"):
                fda_sections.append(f"ADVERSE REACTIONS: {fda_data['adverse_reactions'][:500]}")

        _no_interactions_intents = frozenset({
            "side_effects", "what_is", "dosing", "contraindications", "pregnancy_lactation",
        })
        _include_interactions = intent_str not in _no_interactions_intents

        if len(fda_sections) <= 2:
            if fda_data.get("warnings"):
                fda_sections.append(f"WARNINGS: {fda_data['warnings'][:400]}")
            if fda_data.get("contraindications"):
                fda_sections.append(f"CONTRAINDICATIONS: {fda_data['contraindications'][:400]}")
            if _include_interactions and fda_data.get("drug_interactions"):
                fda_sections.append(f"DRUG INTERACTIONS: {fda_data['drug_interactions'][:400]}")
            if fda_data.get("dosage_and_administration"):
                fda_sections.append(f"DOSAGE AND ADMINISTRATION: {fda_data['dosage_and_administration'][:300]}")

    if medlineplus_data:
        medline_sections.append("MEDLINEPLUS PATIENT SUMMARY")
        medline_sections.append("Use this section only for plain-English explanation and side-effect descriptions.")
        if medlineplus_data.get("summary"):
            medline_sections.append(f"SUMMARY: {medlineplus_data['summary']}")
        if medlineplus_data.get("usage"):
            medline_sections.append(f"PLAIN-ENGLISH USE: {medlineplus_data['usage']}")
        if medlineplus_data.get("side_effects"):
            medline_sections.append(f"PATIENT SIDE EFFECTS: {medlineplus_data['side_effects']}")

    sections: list[str] = []
    if fda_sections:
        sections.append("\n".join(fda_sections))
    if medline_sections:
        sections.append("\n".join(medline_sections))
    return "\n\n".join(sections)


# ── RxNorm ───────────────────────────────────────────────────────────────────

async def fetch_rxnorm_drug_names(limit: int = 1000) -> set[str]:
    """Fetch drug names from RxNorm Prescribe displaynames endpoint."""
    client = _get_client()
    try:
        logger.info("[RxNorm] Fetching drug names from %s...", RXNORM_DISPLAYNAMES_URL)
        resp = await client.get(RXNORM_DISPLAYNAMES_URL, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        terms = data.get("displayTermsList", {}).get("term", [])
        if not isinstance(terms, list):
            terms = [terms] if terms else []
        drugs: set[str] = set()
        for t in terms[:limit]:
            name = str(t).strip().lower()
            if len(name) >= 3 and any(c.isalpha() for c in name):
                drugs.add(name)
                for word in name.split():
                    if len(word) >= 3 and word.isalpha():
                        drugs.add(word)
        logger.info("[RxNorm] Loaded %d medical terms", len(drugs))
        return drugs
    except Exception as e:
        logger.warning("[RxNorm] Failed to fetch drug names: %s", e)
        return set()


async def fetch_rxcui(drug_name: str) -> str | None:
    """Look up RxCUI for a drug name using the RxNorm API."""
    if not drug_name:
        return None
    client = _get_client()
    try:
        resp = await client.get(
            RXNORM_RXCUI_URL,
            params={"name": drug_name, "allSourcesFlag": "0"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        rxcui = data.get("idGroup", {}).get("rxnormId", [None])[0]
        if rxcui:
            logger.info("[RxNorm] RxCUI for '%s': %s", drug_name, rxcui)
        return rxcui
    except Exception as e:
        logger.warning("[RxNorm] Failed to fetch RxCUI for '%s': %s", drug_name, e)
        return None


# ── MedlinePlus ──────────────────────────────────────────────────────────────

async def fetch_medlineplus(drug_name: str) -> dict | None:
    """Fetch patient-friendly drug information from MedlinePlus Connect."""
    if not drug_name:
        return None
    client = _get_client()
    try:
        rxcui = await fetch_rxcui(drug_name)
        if not rxcui:
            return None

        resp = await client.get(
            MEDLINEPLUS_CONNECT_URL,
            params={
                "mainSearchCriteria.v.cs": "2.16.840.1.113883.6.88",
                "mainSearchCriteria.v.c": rxcui,
                "knowledgeResponseType": "application/json",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        feed = data.get("feed", {})
        entries = feed.get("entry", [])
        if not entries:
            return None

        entry = entries[0]
        summary = entry.get("summary", {}).get("_value", "")
        title = entry.get("title", {}).get("_value", "")

        content = entry.get("content", {}).get("_value", "")
        clean = re.sub(r"<[^>]+>", " ", content)
        clean = re.sub(r"\s+", " ", clean).strip()

        result = {
            "summary": (summary or title)[:300],
            "usage": clean[:300] if clean else "",
            "side_effects": "",
        }

        lower_clean = clean.lower()
        se_idx = lower_clean.find("side effect")
        if se_idx != -1:
            result["side_effects"] = clean[se_idx : se_idx + 250]

        if not any(result[k] for k in ("summary", "usage", "side_effects")):
            return None

        logger.info("[MedlinePlus] Fetched info for '%s'", drug_name)
        return result

    except Exception as e:
        logger.warning("[MedlinePlus] Failed for '%s': %s", drug_name, e)
        return None


# ── Pill image ───────────────────────────────────────────────────────────────

async def fetch_pill_image(drug_name: str) -> str | None:
    """Fetch pill photo URL from NIH RxImageAccess."""
    if not drug_name:
        return None
    client = _get_client()
    try:
        resp = await client.get(
            RXIMAGE_API_URL,
            params={"name": drug_name, "resolution": "thumbnail"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("nlmRxImages", [])
        if images:
            image_url = images[0].get("imageUrl")
            if image_url:
                logger.info("[RxImage] Found pill image for '%s'", drug_name)
                return image_url
        return None
    except Exception as e:
        logger.warning("[RxImage] Failed for '%s': %s", drug_name, e)
        return None


# ── PubMed studies ───────────────────────────────────────────────────────────

_pubmed_cache: dict[str, tuple[float, list[dict]]] = {}
_PUBMED_CACHE_TTL = 86400


async def fetch_pubmed_studies(drug_name: str, topic: str = "side effects") -> list[dict]:
    """Fetch related PubMed studies for a drug. Returns max 3 studies."""
    import time

    cache_key = f"{drug_name.lower()}:{topic.lower()}"
    now = time.time()

    if cache_key in _pubmed_cache:
        cached_at, cached_results = _pubmed_cache[cache_key]
        if now - cached_at < _PUBMED_CACHE_TTL:
            return cached_results

    client = _get_client()
    try:
        search_resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": f"{drug_name} {topic}",
                "retmax": "3",
                "retmode": "json",
                "sort": "relevance",
            },
            timeout=5.0,
        )
        if not search_resp.is_success:
            return []
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        summary_resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            timeout=5.0,
        )
        if not summary_resp.is_success:
            return []

        results_dict = summary_resp.json().get("result", {})
        studies = []
        for pmid in ids:
            article = results_dict.get(pmid)
            if not article:
                continue
            title = re.sub(r"<[^>]*>", "", str(article.get("title", ""))).strip()
            journal = str(article.get("fulljournalname", article.get("source", "PubMed"))).strip()
            pubdate = str(article.get("pubdate", ""))
            year_match = re.search(r"\b(19|20)\d{2}\b", pubdate)
            studies.append({
                "title": title,
                "journal": journal,
                "year": year_match.group(0) if year_match else "",
                "pmid": pmid,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })

        _pubmed_cache[cache_key] = (now, studies)
        return studies
    except Exception:
        return []


# ── External source aggregator ───────────────────────────────────────────────

async def fetch_all_external_sources(
    drug_name: str,
    drug_names: list[str],
    intent_str: str,
) -> dict:
    """Parallel-fetch supplemental data from FAERS, FDA Enforcement, and RxNav."""
    from core.cache import ext_cache_get, ext_cache_set

    results: dict = {
        "adverse_events": [],
        "recalls": [],
        "rxnav_interact": [],
        "sources_used": [],
    }
    if not drug_name:
        return results

    norm_name = drug_name.strip().lower()

    async def _ae() -> list:
        cached = await ext_cache_get("adverse_events", norm_name)
        if cached is not None:
            return cached
        try:
            from services.openfda_client import get_adverse_events

            data = get_adverse_events(drug_name)
        except Exception:
            data = []
        await ext_cache_set("adverse_events", norm_name, data)
        return data

    async def _rec() -> list:
        cached = await ext_cache_get("recalls", norm_name)
        if cached is not None:
            return cached
        try:
            from services.openfda_client import get_recalls

            data = get_recalls(drug_name)
        except Exception:
            data = []
        await ext_cache_set("recalls", norm_name, data)
        return data

    async def _rxnav() -> list:
        if intent_str != "interaction" or len(drug_names) < 2:
            return []
        r1 = await fetch_rxcui(drug_names[0])
        r2 = await fetch_rxcui(drug_names[1])
        if not r1 or not r2:
            return []
        cache_key = f"{min(r1, r2)}-{max(r1, r2)}"
        cached = await ext_cache_get("rxnav_interact", cache_key)
        if cached is not None:
            return cached
        try:
            from services.rxnorm_client import get_drug_interactions

            data = get_drug_interactions(r1, r2)
        except Exception:
            data = []
        await ext_cache_set("rxnav_interact", cache_key, data)
        return data

    ae_result, rec_result, rxnav_result = await asyncio.gather(
        _ae(), _rec(), _rxnav(), return_exceptions=True,
    )

    results["adverse_events"] = ae_result if isinstance(ae_result, list) else []
    results["recalls"] = rec_result if isinstance(rec_result, list) else []
    results["rxnav_interact"] = rxnav_result if isinstance(rxnav_result, list) else []

    sources_used: list[str] = []
    if results["adverse_events"]:
        sources_used.append("FDA FAERS")
    if results["recalls"]:
        sources_used.append("FDA Enforcement (recalls)")
    if results["rxnav_interact"]:
        sources_used.append("RxNav/DrugBank")
    results["sources_used"] = sources_used
    return results


def build_enriched_context(ext: dict, intent_str: str) -> str:
    """Build supplemental context block from external source data."""
    parts: list[str] = []

    rxnav = ext.get("rxnav_interact", [])
    if rxnav:
        parts.append("━━━ RXNAV VETTED INTERACTIONS (DrugBank / ONCHigh — authoritative) ━━━")
        for ix in rxnav[:3]:
            sev = (ix.get("severity") or "UNKNOWN").upper()
            desc = ix.get("description", "")
            src = ix.get("source", "")
            d1 = ix.get("drug1", "")
            d2 = ix.get("drug2", "")
            parts.append(f"[{sev}] {d1} + {d2}: {desc} (source: {src})")

    ae = ext.get("adverse_events", [])
    if ae and intent_str in ("side_effects", "what_is", "general"):
        parts.append("━━━ FDA FAERS ADVERSE EVENTS (patient-reported, frequency-ranked) ━━━")
        parts.append("Most frequently reported reactions: " + ", ".join(ae[:12]))

    recalls = ext.get("recalls", [])
    if recalls:
        parts.append("━━━ ACTIVE FDA RECALLS (Class I/II — include in WARNING section) ━━━")
        for r in recalls[:2]:
            cls = r.get("classification", "")
            rsn = r.get("reason_for_recall", "")[:200]
            prod = r.get("product_description", "")[:100]
            parts.append(f"[{cls}] {prod}: {rsn}")

    return "\n\n".join(parts)
