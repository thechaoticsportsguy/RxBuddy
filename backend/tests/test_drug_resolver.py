"""
backend/tests/test_drug_resolver.py
=====================================

Pytest test suite for the drug normalization pipeline:
  - spell_correct.normalize_drug_name
  - drug_resolver.resolve_drug

Run:
  pytest backend/tests/test_drug_resolver.py -v

No external dependencies required for the catalog / spell-correct tests.
RxNorm API tests are marked @pytest.mark.network and skipped in offline mode.
"""

from __future__ import annotations

import os
import sys

import pytest

# Ensure backend/ is on the path so all sibling imports resolve
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.spell_correct import normalize_drug_name
from services.drug_resolver import resolve_drug, resolve_query_drugs


# ── normalize_drug_name ────────────────────────────────────────────────────────

class TestNormalizeDrugName:
    """Offline — no network calls."""

    def test_exact_generic_unchanged(self):
        assert normalize_drug_name("atorvastatin") == "atorvastatin"

    def test_exact_brand_returns_generic(self):
        assert normalize_drug_name("tylenol") == "acetaminophen"

    def test_exact_brand_mixed_case(self):
        assert normalize_drug_name("Tylenol") == "acetaminophen"

    def test_brand_ozempic(self):
        assert normalize_drug_name("ozempic") == "semaglutide"

    def test_brand_eliquis(self):
        assert normalize_drug_name("eliquis") == "apixaban"

    def test_brand_xarelto(self):
        assert normalize_drug_name("xarelto") == "rivaroxaban"

    def test_brand_plavix(self):
        assert normalize_drug_name("plavix") == "clopidogrel"

    def test_brand_zoloft(self):
        assert normalize_drug_name("zoloft") == "sertraline"

    def test_brand_lexapro(self):
        assert normalize_drug_name("lexapro") == "escitalopram"

    def test_brand_prozac(self):
        assert normalize_drug_name("prozac") == "fluoxetine"

    def test_brand_adderall(self):
        assert normalize_drug_name("adderall") == "amphetamine"

    def test_brand_ritalin(self):
        assert normalize_drug_name("ritalin") == "methylphenidate"

    def test_fuzzy_rosuvastin(self):
        # "rosuvastin" → "rosuvastatin" (distance 2: missing "at")
        assert normalize_drug_name("rosuvastin") == "rosuvastatin"

    def test_fuzzy_elyquis(self):
        # "elyquis" → distance 1 from "eliquis" (brand) → generic "apixaban"
        assert normalize_drug_name("elyquis") == "apixaban"

    def test_misspelling_verampril(self):
        # "verampril" is in common_misspellings for verapamil → exact lookup
        assert normalize_drug_name("verampril") == "verapamil"

    def test_misspelling_ibuprofin(self):
        assert normalize_drug_name("ibuprofin") == "ibuprofen"

    def test_misspelling_amoxicillan(self):
        assert normalize_drug_name("amoxicillan") == "amoxicillin"

    def test_misspelling_levothyroxene(self):
        assert normalize_drug_name("levothyroxene") == "levothyroxine"

    def test_unknown_returns_original(self):
        assert normalize_drug_name("xyzzy123abc") == "xyzzy123abc"

    def test_empty_returns_empty(self):
        assert normalize_drug_name("") == ""

    def test_generic_ibuprofen_unchanged(self):
        assert normalize_drug_name("ibuprofen") == "ibuprofen"

    def test_generic_metformin_unchanged(self):
        assert normalize_drug_name("metformin") == "metformin"


# ── resolve_drug ───────────────────────────────────────────────────────────────

class TestResolveDrug:
    """Mostly offline (catalog); RxNorm tests marked separately."""

    def test_eliquis_returns_apixaban(self):
        result = resolve_drug("eliquis")
        assert result.get("generic") == "apixaban", result

    def test_elyquis_misspelling_returns_apixaban(self):
        result = resolve_drug("elyquis")
        assert result.get("generic") == "apixaban", result

    def test_verampril_returns_verapamil(self):
        result = resolve_drug("verampril")
        assert result.get("generic") == "verapamil", result

    def test_rosuvastin_returns_rosuvastatin(self):
        result = resolve_drug("rosuvastin")
        assert result.get("generic") == "rosuvastatin", result

    def test_tylenol_returns_acetaminophen(self):
        result = resolve_drug("tylenol")
        assert result.get("generic") == "acetaminophen", result

    def test_ozempic_returns_semaglutide(self):
        result = resolve_drug("ozempic")
        assert result.get("generic") == "semaglutide", result

    def test_wegovy_returns_semaglutide(self):
        result = resolve_drug("wegovy")
        assert result.get("generic") == "semaglutide", result

    def test_xarelto_returns_rivaroxaban(self):
        result = resolve_drug("xarelto")
        assert result.get("generic") == "rivaroxaban", result

    def test_jardiance_returns_empagliflozin(self):
        result = resolve_drug("jardiance")
        assert result.get("generic") == "empagliflozin", result

    def test_farxiga_returns_dapagliflozin(self):
        result = resolve_drug("farxiga")
        assert result.get("generic") == "dapagliflozin", result

    def test_lipitor_returns_atorvastatin(self):
        result = resolve_drug("lipitor")
        assert result.get("generic") == "atorvastatin", result

    def test_advil_returns_ibuprofen(self):
        result = resolve_drug("advil")
        assert result.get("generic") == "ibuprofen", result

    def test_benadryl_returns_diphenhydramine(self):
        result = resolve_drug("benadryl")
        assert result.get("generic") == "diphenhydramine", result

    def test_result_has_required_keys(self):
        result = resolve_drug("metformin")
        assert result.get("verdict") != "CONSULT"
        for key in ("input", "corrected", "rxcui", "generic", "brands", "synonyms"):
            assert key in result, f"Missing key: {key}"

    def test_brands_is_list(self):
        result = resolve_drug("lipitor")
        assert isinstance(result.get("brands"), list)

    def test_totally_fake_drug_returns_consult(self):
        result = resolve_drug("totally fake drug xyz")
        assert result.get("verdict") == "CONSULT", result
        assert "consult" in result.get("answer", "").lower()

    def test_completely_unknown_string_returns_consult(self):
        result = resolve_drug("qxz99plmk")
        assert result.get("verdict") == "CONSULT"

    def test_empty_input_returns_consult(self):
        result = resolve_drug("")
        assert result.get("verdict") == "CONSULT"


# ── resolve_query_drugs ────────────────────────────────────────────────────────

class TestResolveQueryDrugs:
    """Test free-text query drug extraction."""

    def test_single_drug_query(self):
        results = resolve_query_drugs("can i take eliquis with ibuprofen")
        generics = [r["generic"] for r in results]
        assert "apixaban" in generics, generics
        assert "ibuprofen" in generics, generics

    def test_no_drugs_returns_empty(self):
        results = resolve_query_drugs("is it safe to go for a walk today")
        assert results == []

    def test_brand_in_query_resolved(self):
        results = resolve_query_drugs("ozempic side effects")
        generics = [r["generic"] for r in results]
        assert "semaglutide" in generics, generics

    def test_misspelled_drug_in_query(self):
        results = resolve_query_drugs("can i take ibuprofin with tylenol")
        generics = [r["generic"] for r in results]
        assert "ibuprofen" in generics, generics
        assert "acetaminophen" in generics, generics

    def test_no_duplicates(self):
        results = resolve_query_drugs("ibuprofen ibuprofen ibuprofen")
        generics = [r["generic"] for r in results]
        assert len(generics) == len(set(generics))


# ── RxNorm network tests (opt-in) ─────────────────────────────────────────────

_NETWORK = os.getenv("RXBUDDY_NETWORK") == "1"
_skip_network = pytest.mark.skipif(
    not _NETWORK,
    reason="network tests disabled; set RXBUDDY_NETWORK=1 to enable",
)


@_skip_network
class TestRxNormLookup:
    """Live network tests — skipped unless RXBUDDY_NETWORK=1."""

    def test_rxcui_lookup(self):
        from services.rxnorm_client import get_rxcui
        rxcui = get_rxcui("acetaminophen")
        assert rxcui is not None
        assert rxcui.isdigit()

    def test_search_drug_by_name(self):
        from services.rxnorm_client import search_drug_by_name
        result = search_drug_by_name("warfarin")
        assert result["rxcui"]
        assert "warfarin" in result["name"].lower()

    def test_get_related_drugs(self):
        from services.rxnorm_client import get_rxcui, get_related_drugs
        rxcui = get_rxcui("acetaminophen")
        assert rxcui
        related = get_related_drugs(rxcui)
        assert "acetaminophen" in related.get("generic_name", "").lower()
