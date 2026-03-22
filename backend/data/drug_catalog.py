"""
RxBuddy Drug Catalog — Structured Format
==========================================

Flat list of drug records used by the normalization pipeline.

Each record:
  rxcui              — RxNorm CUI (empty string = not yet resolved via API)
  generic            — canonical generic name (lowercase)
  brands             — list of brand names (mixed case)
  common_misspellings — list of known user misspellings that map to this drug
  dailymed_set_id    — DailyMed SPL setID or None

This module is the source-of-truth for spell_correct.py and drug_resolver.py.
It deliberately avoids importing from the parent drug_catalog.py to prevent
naming conflicts; both files can coexist on sys.path.
"""

from __future__ import annotations

from typing import Optional


DRUG_CATALOG: list[dict] = [
    # ── Analgesics / OTC Pain ─────────────────────────────────────────────────
    {
        "rxcui": "161",
        "generic": "acetaminophen",
        "brands": ["Tylenol", "Panadol", "Mapap", "Paracetamol"],
        "common_misspellings": [
            "tynenol", "tylenol", "acetoaminophen", "acetominophen",
            "acetaminiphen", "acetaminaphen", "acetaminofin",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "5640",
        "generic": "ibuprofen",
        "brands": ["Advil", "Motrin", "Nurofen"],
        "common_misspellings": [
            "advil", "motrin", "ibuprofin", "ibrofen", "ibuprophen",
            "ibupropin", "ibuprofen", "ibuprophen", "ibuprofen",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "naproxen",
        "brands": ["Aleve", "Naprosyn", "Anaprox"],
        "common_misspellings": ["aleve", "naprosyn", "naproxin", "naproxem"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "1191",
        "generic": "aspirin",
        "brands": ["Bayer", "Ecotrin", "Bufferin"],
        "common_misspellings": ["asprin", "asperin", "bayer"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "140587",
        "generic": "celecoxib",
        "brands": ["Celebrex"],
        "common_misspellings": ["celebrex", "celcoxib", "celecoxeb"],
        "dailymed_set_id": None,
    },
    # ── Opioids ───────────────────────────────────────────────────────────────
    {
        "rxcui": "41493",
        "generic": "tramadol",
        "brands": ["Ultram", "ConZip"],
        "common_misspellings": ["tramadal", "tramadol", "tramidol", "ultram"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "3423",
        "generic": "hydrocodone",
        "brands": ["Vicodin", "Norco", "Lortab"],
        "common_misspellings": ["vicodin", "norco", "hydrocodon", "hydracodone"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "7804",
        "generic": "oxycodone",
        "brands": ["OxyContin", "Percocet", "Roxicodone"],
        "common_misspellings": ["oxycontin", "percocet", "oxicodone", "oxycodon"],
        "dailymed_set_id": None,
    },
    # ── Cardiovascular / Statins ──────────────────────────────────────────────
    {
        "rxcui": "83367",
        "generic": "atorvastatin",
        "brands": ["Lipitor"],
        "common_misspellings": ["lipitor", "atorvastain", "atorvasatin", "atorvastatin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "301542",
        "generic": "rosuvastatin",
        "brands": ["Crestor", "Ezallor"],
        "common_misspellings": [
            "crestor", "rosuvastin", "rosuvastain", "rosavastatin",
            "rosuvastatин", "rosuvastatan",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "36567",
        "generic": "simvastatin",
        "brands": ["Zocor"],
        "common_misspellings": ["zocor", "simvasatin", "simvastatin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "42463",
        "generic": "pravastatin",
        "brands": ["Pravachol"],
        "common_misspellings": ["pravachol", "pravastatин"],
        "dailymed_set_id": None,
    },
    # ── Cardiovascular / Antihypertensives ────────────────────────────────────
    {
        "rxcui": "29046",
        "generic": "lisinopril",
        "brands": ["Zestril", "Prinivil"],
        "common_misspellings": ["zestril", "lisinoprel", "lisinoproll", "lisenopril"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "17767",
        "generic": "amlodipine",
        "brands": ["Norvasc"],
        "common_misspellings": ["norvasc", "amlodipene", "amlodipin", "amlodipine"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "metoprolol",
        "brands": ["Lopressor", "Toprol XL"],
        "common_misspellings": ["lopressor", "metoprolal", "metoprololol", "metprolol"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "33910",
        "generic": "verapamil",
        "brands": ["Calan", "Isoptin", "Verelan"],
        "common_misspellings": [
            "calan", "verelan", "verampril", "veramill", "verapamill",
            "veropamil", "verapamol", "verapamil",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "321064",
        "generic": "losartan",
        "brands": ["Cozaar"],
        "common_misspellings": ["cozaar", "lozartan", "losarton"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "69749",
        "generic": "furosemide",
        "brands": ["Lasix"],
        "common_misspellings": ["lasix", "furosimide", "furosamide"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "5487",
        "generic": "hydrochlorothiazide",
        "brands": ["Microzide", "HydroDIURIL"],
        "common_misspellings": ["hctz", "hydrochlorothazide", "hydrochlorothiazide"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "9997",
        "generic": "spironolactone",
        "brands": ["Aldactone", "CaroSpir"],
        "common_misspellings": ["aldactone", "spirinolactone", "spironolacton"],
        "dailymed_set_id": None,
    },
    # ── Anticoagulants ────────────────────────────────────────────────────────
    {
        "rxcui": "11289",
        "generic": "warfarin",
        "brands": ["Coumadin", "Jantoven"],
        "common_misspellings": ["coumadin", "warfrin", "warfaren", "warfarin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "1364430",
        "generic": "apixaban",
        "brands": ["Eliquis"],
        "common_misspellings": [
            "eliquis", "elyquis", "eliqus", "eliiquis",
            "apixiban", "apixoban", "apixabin",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "1114195",
        "generic": "rivaroxaban",
        "brands": ["Xarelto"],
        "common_misspellings": [
            "xarelto", "rivaroxiban", "rivaroxaban", "xareltо",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "613391",
        "generic": "dabigatran",
        "brands": ["Pradaxa"],
        "common_misspellings": ["pradaxa", "dabigatren", "dabigitran"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "32968",
        "generic": "clopidogrel",
        "brands": ["Plavix"],
        "common_misspellings": ["plavix", "clopidagryl", "clopidogral"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "704",
        "generic": "ticagrelor",
        "brands": ["Brilinta"],
        "common_misspellings": ["brilinta", "ticagrelar", "ticagrelor"],
        "dailymed_set_id": None,
    },
    # ── Cardiac ───────────────────────────────────────────────────────────────
    {
        "rxcui": "3407",
        "generic": "digoxin",
        "brands": ["Lanoxin"],
        "common_misspellings": ["lanoxin", "digoxen", "digocin", "digoxin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "703",
        "generic": "amiodarone",
        "brands": ["Pacerone", "Cordarone"],
        "common_misspellings": ["cordarone", "amiodarone", "amiodaron", "amiodarona"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "321827",
        "generic": "sacubitril",
        "brands": ["Entresto"],
        "common_misspellings": ["entresto", "sacubitrel"],
        "dailymed_set_id": None,
    },
    # ── Diabetes ─────────────────────────────────────────────────────────────
    {
        "rxcui": "6809",
        "generic": "metformin",
        "brands": ["Glucophage", "Fortamet"],
        "common_misspellings": ["glucophage", "metforman", "metformin", "metformine"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2200806",
        "generic": "semaglutide",
        "brands": ["Ozempic", "Wegovy", "Rybelsus"],
        "common_misspellings": [
            "ozempic", "wegovy", "rybelsus", "semoglutide",
            "semaglitude", "semaglutid", "semiglutide",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2200801",
        "generic": "empagliflozin",
        "brands": ["Jardiance"],
        "common_misspellings": ["jardiance", "empagliflozine", "empagliflozin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "1488568",
        "generic": "dapagliflozin",
        "brands": ["Farxiga"],
        "common_misspellings": ["farxiga", "dapagliflozine", "farxega"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2200802",
        "generic": "dulaglutide",
        "brands": ["Trulicity"],
        "common_misspellings": ["trulicity", "dulaglitude", "duloglutide"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "475968",
        "generic": "liraglutide",
        "brands": ["Victoza", "Saxenda"],
        "common_misspellings": ["victoza", "saxenda", "liraglitude"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2200803",
        "generic": "tirzepatide",
        "brands": ["Mounjaro", "Zepbound"],
        "common_misspellings": ["mounjaro", "zepbound", "tirzepatid"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "86009",
        "generic": "insulin glargine",
        "brands": ["Lantus", "Basaglar", "Toujeo"],
        "common_misspellings": ["lantus", "insulin", "insuline", "basaglar"],
        "dailymed_set_id": None,
    },
    # ── GI / Acid ────────────────────────────────────────────────────────────
    {
        "rxcui": "7646",
        "generic": "omeprazole",
        "brands": ["Prilosec", "Zegerid"],
        "common_misspellings": ["prilosec", "omeprazol", "omeprazole", "omprazole"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "40790",
        "generic": "esomeprazole",
        "brands": ["Nexium"],
        "common_misspellings": ["nexium", "esomeprazol", "esomeprazole"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "17128",
        "generic": "famotidine",
        "brands": ["Pepcid", "Zantac 360"],
        "common_misspellings": ["pepcid", "famotidene", "famotidine"],
        "dailymed_set_id": None,
    },
    # ── Thyroid ───────────────────────────────────────────────────────────────
    {
        "rxcui": "10582",
        "generic": "levothyroxine",
        "brands": ["Synthroid", "Levoxyl", "Tirosint"],
        "common_misspellings": [
            "synthroid", "levothyroxene", "levothyroxin", "levothyroxeine",
        ],
        "dailymed_set_id": None,
    },
    # ── CNS / Psychiatry ──────────────────────────────────────────────────────
    {
        "rxcui": "36437",
        "generic": "sertraline",
        "brands": ["Zoloft"],
        "common_misspellings": ["zoloft", "sertralene", "sertralin", "sertriline"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "227224",
        "generic": "escitalopram",
        "brands": ["Lexapro", "Cipralex"],
        "common_misspellings": [
            "lexapro", "escitalopam", "escitalpram", "escitalopram",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "fluoxetine",
        "brands": ["Prozac", "Sarafem"],
        "common_misspellings": ["prozac", "fluoxatine", "fluoxitine", "fluoxetene"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "32937",
        "generic": "paroxetine",
        "brands": ["Paxil", "Brisdelle"],
        "common_misspellings": ["paxil", "paroxatine", "paroxitine"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "venlafaxine",
        "brands": ["Effexor", "Effexor XR"],
        "common_misspellings": ["effexor", "venlafaxene", "venlafaxin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "72625",
        "generic": "duloxetine",
        "brands": ["Cymbalta", "Drizalma"],
        "common_misspellings": ["cymbalta", "duloxetene", "duloxitin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "42347",
        "generic": "bupropion",
        "brands": ["Wellbutrin", "Zyban"],
        "common_misspellings": ["wellbutrin", "bupropian", "buproprion", "bupropeon"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "698",
        "generic": "trazodone",
        "brands": ["Desyrel", "Oleptro"],
        "common_misspellings": ["desyrel", "trazodene", "trazodon"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "5012",
        "generic": "lithium",
        "brands": ["Lithobid", "Eskalith"],
        "common_misspellings": ["lithobid", "eskalith", "litium"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "quetiapine",
        "brands": ["Seroquel", "Seroquel XR"],
        "common_misspellings": ["seroquel", "quetiapene", "quetiapine"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41308",
        "generic": "aripiprazole",
        "brands": ["Abilify", "Aristada"],
        "common_misspellings": ["abilify", "aripiprazol", "arippiprazole"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "321064",
        "generic": "naltrexone",
        "brands": ["Vivitrol", "ReVia"],
        "common_misspellings": ["vivitrol", "naltexone", "naltrexon"],
        "dailymed_set_id": None,
    },
    # ── Benzodiazepines ───────────────────────────────────────────────────────
    {
        "rxcui": "596",
        "generic": "alprazolam",
        "brands": ["Xanax"],
        "common_misspellings": ["xanax", "alprazolem", "alprazolam"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "6470",
        "generic": "lorazepam",
        "brands": ["Ativan"],
        "common_misspellings": ["ativan", "lorazepem", "lorazipam"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2598",
        "generic": "clonazepam",
        "brands": ["Klonopin"],
        "common_misspellings": ["klonopin", "clonazepem", "clonazapam"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "3322",
        "generic": "diazepam",
        "brands": ["Valium"],
        "common_misspellings": ["valium", "diazepem", "diazapam"],
        "dailymed_set_id": None,
    },
    # ── Sleep aids ────────────────────────────────────────────────────────────
    {
        "rxcui": "41493",
        "generic": "zolpidem",
        "brands": ["Ambien", "Edluar"],
        "common_misspellings": ["ambien", "zolpidum", "zolpidim"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "115698",
        "generic": "melatonin",
        "brands": ["Natrol", "ZzzQuil Pure Zzzs"],
        "common_misspellings": ["melotonin", "melatonine", "melatonin"],
        "dailymed_set_id": None,
    },
    # ── ADHD / Stimulants ─────────────────────────────────────────────────────
    {
        "rxcui": "7243",
        "generic": "amphetamine",
        "brands": ["Adderall", "Vyvanse", "Dexedrine"],
        "common_misspellings": ["adderall", "vyvanse", "adderal", "adderrall"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "9560",
        "generic": "methylphenidate",
        "brands": ["Ritalin", "Concerta", "Quillivant"],
        "common_misspellings": [
            "ritalin", "concerta", "methylfenidate", "methylphendate",
        ],
        "dailymed_set_id": None,
    },
    # ── Neurology / AED ──────────────────────────────────────────────────────
    {
        "rxcui": "25480",
        "generic": "gabapentin",
        "brands": ["Neurontin", "Gralise"],
        "common_misspellings": ["neurontin", "gabapentine", "gabapenten"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "187832",
        "generic": "pregabalin",
        "brands": ["Lyrica"],
        "common_misspellings": ["lyrica", "pregabaline", "pregabolin"],
        "dailymed_set_id": None,
    },
    # ── Respiratory ──────────────────────────────────────────────────────────
    {
        "rxcui": "435",
        "generic": "albuterol",
        "brands": ["ProAir", "Ventolin", "Proventil"],
        "common_misspellings": ["proair", "ventolin", "albuterel", "albuerol"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "19831",
        "generic": "budesonide",
        "brands": ["Pulmicort", "Rhinocort"],
        "common_misspellings": ["pulmicort", "budesonid", "budesoniide"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "fluticasone",
        "brands": ["Flovent", "Flonase"],
        "common_misspellings": ["flonase", "flovent", "fluticasone", "fluticazone"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "montelukast",
        "brands": ["Singulair"],
        "common_misspellings": ["singulair", "montelukaste", "montelucast"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "41493",
        "generic": "prednisone",
        "brands": ["Deltasone", "Rayos"],
        "common_misspellings": ["deltasone", "prednisone", "prednisoline", "prednizone"],
        "dailymed_set_id": None,
    },
    # ── Antihistamines ────────────────────────────────────────────────────────
    {
        "rxcui": "3498",
        "generic": "diphenhydramine",
        "brands": ["Benadryl", "ZzzQuil", "Unisom"],
        "common_misspellings": [
            "benadryl", "diphenhydromine", "diphenhydramine", "benadril",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "17115",
        "generic": "loratadine",
        "brands": ["Claritin", "Alavert"],
        "common_misspellings": ["claritin", "loratidine", "loratadene"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "20489",
        "generic": "cetirizine",
        "brands": ["Zyrtec", "Reactine"],
        "common_misspellings": ["zyrtec", "cetirizene", "cetrizine", "cetirazine"],
        "dailymed_set_id": None,
    },
    # ── Antibiotics ───────────────────────────────────────────────────────────
    {
        "rxcui": "723",
        "generic": "amoxicillin",
        "brands": ["Amoxil", "Trimox"],
        "common_misspellings": [
            "amoxil", "amoxicillan", "amoxicilin", "amoxocillin", "amoxacillin",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "18631",
        "generic": "azithromycin",
        "brands": ["Zithromax", "Z-Pack"],
        "common_misspellings": [
            "zithromax", "azithromicin", "azithromycin", "azithromycen",
        ],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "2551",
        "generic": "ciprofloxacin",
        "brands": ["Cipro"],
        "common_misspellings": ["cipro", "ciprofloxacine", "ciprafloxacin"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "25037",
        "generic": "doxycycline",
        "brands": ["Vibramycin", "Doryx"],
        "common_misspellings": ["vibramycin", "doxycyclin", "doxycycline"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "7454",
        "generic": "metronidazole",
        "brands": ["Flagyl"],
        "common_misspellings": ["flagyl", "metronidazol", "metronidazole"],
        "dailymed_set_id": None,
    },
    # ── Erectile Dysfunction / Sexual Health ──────────────────────────────────
    {
        "rxcui": "1112218",
        "generic": "sildenafil",
        "brands": ["Viagra", "Revatio"],
        "common_misspellings": ["viagra", "sildenafile", "sildenafil"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "1113452",
        "generic": "tadalafil",
        "brands": ["Cialis", "Adcirca"],
        "common_misspellings": ["cialis", "tadalafile", "tadalafil"],
        "dailymed_set_id": None,
    },
    # ── Rheumatology / Immunology ────────────────────────────────────────────
    {
        "rxcui": "105585",
        "generic": "adalimumab",
        "brands": ["Humira"],
        "common_misspellings": ["humira", "adalimumab"],
        "dailymed_set_id": None,
    },
    {
        "rxcui": "352363",
        "generic": "dupilumab",
        "brands": ["Dupixent"],
        "common_misspellings": ["dupixent", "dupilumab"],
        "dailymed_set_id": None,
    },
]


# ── Derived lookups built at import time ─────────────────────────────────────

# All names (lowercase) → generic name
_EXACT_TO_GENERIC: dict[str, str] = {}

# (name_lower, generic) pairs for fuzzy matching — ordered by name length
# so shorter strings are checked first (faster distance computation)
_FUZZY_PAIRS: list[tuple[str, str]] = []


def _build_lookups() -> None:
    seen: set[str] = set()
    for entry in DRUG_CATALOG:
        generic = entry["generic"].lower()
        names: list[str] = [generic] + [b.lower() for b in entry["brands"]] + [
            m.lower() for m in entry["common_misspellings"]
        ]
        for name in names:
            _EXACT_TO_GENERIC[name] = generic
            if name not in seen:
                seen.add(name)
                _FUZZY_PAIRS.append((name, generic))

    # Sort by length so the distance cutoff prunes more candidates early
    _FUZZY_PAIRS.sort(key=lambda t: len(t[0]))


_build_lookups()


def get_exact_to_generic() -> dict[str, str]:
    """Return mapping: any known name (lowercase) → generic name."""
    return _EXACT_TO_GENERIC


def get_fuzzy_pairs() -> list[tuple[str, str]]:
    """Return list of (name_lower, generic) for Levenshtein fuzzy matching."""
    return _FUZZY_PAIRS


def lookup_generic(name: str) -> Optional[str]:
    """Exact lookup: any known name → generic. Returns None if not found."""
    return _EXACT_TO_GENERIC.get((name or "").strip().lower())
