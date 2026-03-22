"""
RxBuddy Drug Catalog — v1
==========================

Maintains a curated list of ~200 target drugs (top US prescriptions + common OTCs)
with RxNorm CUI, brand/generic mappings, drug class, and DailyMed setID.

Usage
-----
  from drug_catalog import find_drug, preload_catalog, DrugRecord

  record = find_drug("Tylenol")       # → DrugRecord(canonical="acetaminophen", ...)
  record = find_drug("ibuprofin")     # → DrugRecord via RxNorm spell-correction
  preload_catalog()                   # call at startup to warm the cache

Data refresh
------------
  POST /admin/refresh-catalog → calls preload_catalog(force=True)
  The catalog RxCUI/setID entries are fetched lazily and cached in-process (lru_cache).

Data priority
-------------
  FDA label (OpenFDA/DailyMed) > RxNorm canonical > drug_catalog defaults
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("rxbuddy.drug_catalog")


# ── DrugRecord ───────────────────────────────────────────────────────────────

@dataclass
class DrugRecord:
    canonical_name: str
    rxcui:          Optional[str]       = None
    brand_names:    list[str]           = field(default_factory=list)
    generic_names:  list[str]           = field(default_factory=list)
    drug_class:     str                 = "General"
    dailymed_setid: Optional[str]       = None
    nda_number:     Optional[str]       = None
    is_high_risk:   bool                = False  # narrow TI or known dangerous

    @property
    def all_names(self) -> list[str]:
        """All known names for this drug (canonical + brands + generics), lowercase."""
        return [self.canonical_name] + self.brand_names + self.generic_names

    def dailymed_url(self) -> Optional[str]:
        if self.dailymed_setid:
            return f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={self.dailymed_setid}"
        return f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query={self.canonical_name}"


# ── Seed data ─────────────────────────────────────────────────────────────────
# Tuple: (canonical_name, [brand_names], drug_class, is_high_risk)
# Source: CMS top-prescribed medications, ASHP, IMS Health, FDA Orange Book.

_SEED: list[tuple[str, list[str], str, bool]] = [
    # ── Analgesics / Anti-inflammatory ───────────────────────────────────────
    ("acetaminophen",   ["Tylenol", "Panadol", "Paracetamol", "Mapap"], "Analgesic",          False),
    ("ibuprofen",       ["Advil", "Motrin", "Nurofen"],                  "NSAID",               False),
    ("naproxen",        ["Aleve", "Naprosyn", "Anaprox"],               "NSAID",               False),
    ("aspirin",         ["Bayer", "Ecotrin", "Bufferin"],                "NSAID/Antiplatelet",  False),
    ("celecoxib",       ["Celebrex"],                                    "NSAID (COX-2)",       False),
    ("meloxicam",       ["Mobic"],                                       "NSAID",               False),
    ("diclofenac",      ["Voltaren", "Cambia", "Zipsor"],               "NSAID",               False),
    ("indomethacin",    ["Indocin", "Tivorbex"],                        "NSAID",               False),
    ("ketorolac",       ["Toradol"],                                     "NSAID",               False),
    ("tramadol",        ["Ultram", "ConZip"],                            "Opioid Analgesic",    True),
    ("hydrocodone",     ["Vicodin", "Norco", "Lortab"],                 "Opioid Analgesic",    True),
    ("oxycodone",       ["OxyContin", "Percocet", "Roxicodone"],        "Opioid Analgesic",    True),
    ("morphine",        ["MS Contin", "Kadian", "MSIR"],                "Opioid Analgesic",    True),
    ("fentanyl",        ["Duragesic", "Sublimaze", "Actiq"],            "Opioid Analgesic",    True),
    ("codeine",         ["Tylenol with Codeine"],                        "Opioid Analgesic",    True),
    ("buprenorphine",   ["Suboxone", "Subutex", "Buprenex"],           "Opioid Partial Agonist", True),
    ("naloxone",        ["Narcan", "Evzio"],                            "Opioid Antagonist",   False),
    ("methadone",       ["Dolophine", "Methadose"],                     "Opioid",              True),
    # ── Cardiovascular ───────────────────────────────────────────────────────
    ("atorvastatin",    ["Lipitor"],                                     "Statin",              False),
    ("rosuvastatin",    ["Crestor", "Ezallor"],                         "Statin",              False),
    ("simvastatin",     ["Zocor"],                                       "Statin",              False),
    ("pravastatin",     ["Pravachol"],                                   "Statin",              False),
    ("lovastatin",      ["Mevacor", "Altoprev"],                        "Statin",              False),
    ("pitavastatin",    ["Livalo"],                                      "Statin",              False),
    ("ezetimibe",       ["Zetia", "Ezetrol"],                           "Lipid-Lowering",      False),
    ("fenofibrate",     ["TriCor", "Fenoglide", "Tricor"],              "Fibrate",             False),
    ("lisinopril",      ["Zestril", "Prinivil"],                        "ACE Inhibitor",       False),
    ("enalapril",       ["Vasotec"],                                     "ACE Inhibitor",       False),
    ("ramipril",        ["Altace"],                                      "ACE Inhibitor",       False),
    ("benazepril",      ["Lotensin"],                                    "ACE Inhibitor",       False),
    ("captopril",       ["Capoten"],                                     "ACE Inhibitor",       False),
    ("losartan",        ["Cozaar"],                                      "ARB",                 False),
    ("valsartan",       ["Diovan"],                                      "ARB",                 False),
    ("olmesartan",      ["Benicar"],                                     "ARB",                 False),
    ("irbesartan",      ["Avapro"],                                      "ARB",                 False),
    ("candesartan",     ["Atacand"],                                     "ARB",                 False),
    ("telmisartan",     ["Micardis"],                                    "ARB",                 False),
    ("amlodipine",      ["Norvasc"],                                     "Calcium Channel Blocker", False),
    ("nifedipine",      ["Procardia", "Adalat"],                        "Calcium Channel Blocker", False),
    ("diltiazem",       ["Cardizem", "Tiazac", "Cartia XT"],           "Calcium Channel Blocker", False),
    ("verapamil",       ["Calan", "Isoptin", "Verelan"],               "Calcium Channel Blocker", True),
    ("metoprolol",      ["Lopressor", "Toprol XL"],                    "Beta Blocker",         False),
    ("atenolol",        ["Tenormin"],                                    "Beta Blocker",         False),
    ("carvedilol",      ["Coreg"],                                       "Beta Blocker",         False),
    ("propranolol",     ["Inderal", "InnoPran XL"],                    "Beta Blocker",         False),
    ("bisoprolol",      ["Zebeta"],                                      "Beta Blocker",         False),
    ("nebivolol",       ["Bystolic"],                                    "Beta Blocker",         False),
    ("hydrochlorothiazide", ["Microzide", "HydroDIURIL"],              "Thiazide Diuretic",    False),
    ("chlorthalidone",  ["Hygroton", "Thalitone"],                      "Thiazide Diuretic",    False),
    ("furosemide",      ["Lasix"],                                       "Loop Diuretic",        False),
    ("torsemide",       ["Demadex"],                                     "Loop Diuretic",        False),
    ("bumetanide",      ["Bumex"],                                       "Loop Diuretic",        False),
    ("spironolactone",  ["Aldactone", "CaroSpir"],                      "Potassium-Sparing Diuretic", False),
    ("eplerenone",      ["Inspra"],                                      "Potassium-Sparing Diuretic", False),
    ("digoxin",         ["Lanoxin"],                                     "Cardiac Glycoside",    True),
    ("warfarin",        ["Coumadin", "Jantoven"],                       "Anticoagulant",        True),
    ("apixaban",        ["Eliquis"],                                     "Anticoagulant (DOAC)", True),
    ("rivaroxaban",     ["Xarelto"],                                     "Anticoagulant (DOAC)", True),
    ("dabigatran",      ["Pradaxa"],                                     "Anticoagulant (DOAC)", True),
    ("edoxaban",        ["Savaysa"],                                     "Anticoagulant (DOAC)", True),
    ("heparin",         ["Heparin Sodium"],                              "Anticoagulant",        True),
    ("enoxaparin",      ["Lovenox", "Clexane"],                        "Anticoagulant (LMWH)", True),
    ("clopidogrel",     ["Plavix"],                                      "Antiplatelet",         True),
    ("prasugrel",       ["Effient"],                                     "Antiplatelet",         True),
    ("ticagrelor",      ["Brilinta"],                                    "Antiplatelet",         True),
    ("amiodarone",      ["Pacerone", "Cordarone"],                      "Antiarrhythmic",       True),
    ("sotalol",         ["Betapace", "Sorine"],                         "Antiarrhythmic",       True),
    ("flecainide",      ["Tambocor"],                                    "Antiarrhythmic",       True),
    ("dronedarone",     ["Multaq"],                                      "Antiarrhythmic",       True),
    ("sacubitril",      ["Entresto"],                                    "ARNi",                 False),
    ("hydralazine",     ["Apresoline"],                                  "Vasodilator",          False),
    ("isosorbide mononitrate", ["Imdur", "ISMO"],                      "Nitrate",              False),
    ("nitroglycerin",   ["Nitrostat", "Nitrolingual", "NitroMist"],    "Nitrate",              False),
    ("ivabradine",      ["Corlanor"],                                    "Funny Channel Inhibitor", False),
    # ── Diabetes ─────────────────────────────────────────────────────────────
    ("metformin",       ["Glucophage", "Fortamet", "Riomet"],          "Biguanide",            False),
    ("glipizide",       ["Glucotrol"],                                   "Sulfonylurea",         False),
    ("glimepiride",     ["Amaryl"],                                      "Sulfonylurea",         False),
    ("glyburide",       ["DiaBeta", "Micronase", "Glynase"],           "Sulfonylurea",         False),
    ("pioglitazone",    ["Actos"],                                       "Thiazolidinedione",    False),
    ("sitagliptin",     ["Januvia"],                                     "DPP-4 Inhibitor",      False),
    ("saxagliptin",     ["Onglyza"],                                     "DPP-4 Inhibitor",      False),
    ("linagliptin",     ["Tradjenta"],                                   "DPP-4 Inhibitor",      False),
    ("alogliptin",      ["Nesina"],                                      "DPP-4 Inhibitor",      False),
    ("empagliflozin",   ["Jardiance"],                                   "SGLT-2 Inhibitor",     False),
    ("dapagliflozin",   ["Farxiga"],                                     "SGLT-2 Inhibitor",     False),
    ("canagliflozin",   ["Invokana"],                                    "SGLT-2 Inhibitor",     False),
    ("ertugliflozin",   ["Steglatro"],                                   "SGLT-2 Inhibitor",     False),
    ("liraglutide",     ["Victoza", "Saxenda"],                         "GLP-1 Agonist",        False),
    ("semaglutide",     ["Ozempic", "Wegovy", "Rybelsus"],             "GLP-1 Agonist",        False),
    ("dulaglutide",     ["Trulicity"],                                   "GLP-1 Agonist",        False),
    ("exenatide",       ["Byetta", "Bydureon"],                        "GLP-1 Agonist",        False),
    ("tirzepatide",     ["Mounjaro", "Zepbound"],                      "GIP/GLP-1 Agonist",    False),
    ("insulin glargine",  ["Lantus", "Basaglar", "Toujeo"],            "Insulin",              True),
    ("insulin detemir",   ["Levemir"],                                  "Insulin",              True),
    ("insulin degludec",  ["Tresiba"],                                  "Insulin",              True),
    ("insulin aspart",    ["NovoLog", "Fiasp"],                        "Insulin",              True),
    ("insulin lispro",    ["Humalog", "Admelog"],                      "Insulin",              True),
    ("insulin regular",   ["Humulin R", "Novolin R"],                  "Insulin",              True),
    ("acarbose",        ["Precose"],                                     "Alpha-Glucosidase Inhibitor", False),
    ("repaglinide",     ["Prandin"],                                     "Meglitinide",          False),
    # ── Respiratory ──────────────────────────────────────────────────────────
    ("albuterol",       ["ProAir", "Ventolin", "Proventil", "AccuNeb"], "SABA",                False),
    ("levalbuterol",    ["Xopenex"],                                     "SABA",                False),
    ("salmeterol",      ["Serevent"],                                    "LABA",                False),
    ("formoterol",      ["Foradil", "Perforomist"],                     "LABA",                False),
    ("tiotropium",      ["Spiriva"],                                     "LAMA",                False),
    ("umeclidinium",    ["Incruse Ellipta"],                             "LAMA",                False),
    ("ipratropium",     ["Atrovent"],                                    "Anticholinergic Bronchodilator", False),
    ("fluticasone",     ["Flovent", "Flonase", "Arnuity"],             "ICS",                 False),
    ("budesonide",      ["Pulmicort", "Rhinocort", "Entocort"],        "ICS",                 False),
    ("beclomethasone",  ["QVAR", "Beconase"],                          "ICS",                 False),
    ("mometasone",      ["Nasonex", "Asmanex"],                        "ICS",                 False),
    ("ciclesonide",     ["Alvesco", "Omnaris"],                        "ICS",                 False),
    ("montelukast",     ["Singulair"],                                   "Leukotriene Antagonist", False),
    ("roflumilast",     ["Daliresp"],                                    "PDE-4 Inhibitor",     False),
    ("theophylline",    ["Theo-Dur", "Uniphyl", "Theo-24"],            "Bronchodilator",       True),
    ("prednisone",      ["Deltasone", "Rayos"],                        "Corticosteroid",       False),
    ("methylprednisolone", ["Medrol", "Depo-Medrol", "Solu-Medrol"],  "Corticosteroid",       False),
    ("dexamethasone",   ["Decadron", "Dexamethasone Intensol"],        "Corticosteroid",       False),
    ("triamcinolone",   ["Kenalog", "Nasacort", "Trivaris"],          "Corticosteroid",       False),
    ("cetirizine",      ["Zyrtec", "Reactine"],                        "Antihistamine (2nd gen)", False),
    ("loratadine",      ["Claritin", "Alavert"],                       "Antihistamine (2nd gen)", False),
    ("fexofenadine",    ["Allegra"],                                    "Antihistamine (2nd gen)", False),
    ("desloratadine",   ["Clarinex"],                                   "Antihistamine (2nd gen)", False),
    ("levocetirizine",  ["Xyzal"],                                      "Antihistamine (2nd gen)", False),
    ("diphenhydramine", ["Benadryl", "ZzzQuil", "Unisom"],            "Antihistamine (1st gen)", False),
    ("pseudoephedrine", ["Sudafed"],                                    "Decongestant",         False),
    ("guaifenesin",     ["Mucinex", "Robitussin"],                     "Expectorant",          False),
    ("dextromethorphan",["Robitussin DM", "Delsym", "NyQuil"],        "Antitussive",          False),
    ("benralizumab",    ["Fasenra"],                                    "Biologic (Anti-IL-5Ra)", False),
    ("mepolizumab",     ["Nucala"],                                     "Biologic (Anti-IL-5)", False),
    ("dupilumab",       ["Dupixent"],                                   "Biologic (Anti-IL-4/13)", False),
    ("omalizumab",      ["Xolair"],                                     "Biologic (Anti-IgE)", False),
    # ── GI / Acid ────────────────────────────────────────────────────────────
    ("omeprazole",      ["Prilosec", "Zegerid"],                       "PPI",                 False),
    ("pantoprazole",    ["Protonix", "Pantoloc"],                      "PPI",                 False),
    ("esomeprazole",    ["Nexium"],                                     "PPI",                 False),
    ("lansoprazole",    ["Prevacid"],                                   "PPI",                 False),
    ("rabeprazole",     ["Aciphex"],                                    "PPI",                 False),
    ("famotidine",      ["Pepcid", "Zantac 360"],                      "H2 Blocker",          False),
    ("sucralfate",      ["Carafate"],                                   "Mucosal Protectant",  False),
    ("misoprostol",     ["Cytotec"],                                    "Prostaglandin",        True),
    ("ondansetron",     ["Zofran", "Zuplenz"],                         "Antiemetic (5-HT3)",  False),
    ("metoclopramide",  ["Reglan"],                                     "Prokinetic",           False),
    ("prochlorperazine",["Compazine"],                                  "Antiemetic",           False),
    ("loperamide",      ["Imodium"],                                    "Antidiarrheal",        False),
    ("polyethylene glycol", ["MiraLax", "GlycoLax"],                   "Osmotic Laxative",    False),
    ("bisacodyl",       ["Dulcolax"],                                   "Stimulant Laxative",  False),
    ("docusate",        ["Colace", "Surfak"],                          "Stool Softener",       False),
    ("senna",           ["Senokot", "Ex-Lax"],                        "Stimulant Laxative",  False),
    ("lactulose",       ["Enulose", "Kristalose"],                     "Osmotic Laxative",    False),
    ("linaclotide",     ["Linzess"],                                    "GC-C Agonist",         False),
    ("lubiprostone",    ["Amitiza"],                                    "Chloride Channel Activator", False),
    # ── CNS / Psychiatry ─────────────────────────────────────────────────────
    ("sertraline",      ["Zoloft"],                                     "SSRI",                False),
    ("escitalopram",    ["Lexapro", "Cipralex"],                       "SSRI",                False),
    ("fluoxetine",      ["Prozac", "Sarafem"],                        "SSRI",                False),
    ("paroxetine",      ["Paxil", "Brisdelle", "Pexeva"],            "SSRI",                False),
    ("citalopram",      ["Celexa"],                                     "SSRI",                False),
    ("fluvoxamine",     ["Luvox"],                                      "SSRI",                False),
    ("venlafaxine",     ["Effexor", "Effexor XR"],                    "SNRI",                False),
    ("duloxetine",      ["Cymbalta", "Drizalma"],                      "SNRI",                False),
    ("desvenlafaxine",  ["Pristiq"],                                    "SNRI",                False),
    ("mirtazapine",     ["Remeron"],                                    "NaSSA",               False),
    ("trazodone",       ["Desyrel", "Oleptro"],                       "SARI",                False),
    ("bupropion",       ["Wellbutrin", "Zyban", "Aplenzin"],          "NDRI",                False),
    ("amitriptyline",   ["Elavil"],                                    "TCA",                 True),
    ("nortriptyline",   ["Pamelor", "Aventyl"],                        "TCA",                 True),
    ("imipramine",      ["Tofranil"],                                   "TCA",                 True),
    ("clomipramine",    ["Anafranil"],                                  "TCA",                 True),
    ("lithium",         ["Lithobid", "Eskalith"],                      "Mood Stabilizer",      True),
    ("valproic acid",   ["Depakote", "Depakene", "Stavzor"],         "Mood Stabilizer/AED", True),
    ("lamotrigine",     ["Lamictal"],                                   "Mood Stabilizer/AED", False),
    ("carbamazepine",   ["Tegretol", "Carbatrol", "Equetro"],        "AED",                 True),
    ("oxcarbazepine",   ["Trileptal", "Oxtellar XR"],                  "AED",                 False),
    ("phenytoin",       ["Dilantin", "Phenytek"],                      "AED",                 True),
    ("phenobarbital",   ["Luminal"],                                    "AED/Barbiturate",      True),
    ("gabapentin",      ["Neurontin", "Gralise"],                      "AED/Analgesic",        False),
    ("pregabalin",      ["Lyrica"],                                     "AED/Analgesic",        False),
    ("levetiracetam",   ["Keppra", "Keppra XR"],                      "AED",                 False),
    ("topiramate",      ["Topamax", "Qudexy XR", "Trokendi XR"],     "AED",                 False),
    ("zonisamide",      ["Zonegran"],                                   "AED",                 False),
    ("lacosamide",      ["Vimpat"],                                     "AED",                 False),
    ("aripiprazole",    ["Abilify", "Aristada"],                       "Atypical Antipsychotic", False),
    ("quetiapine",      ["Seroquel", "Seroquel XR"],                  "Atypical Antipsychotic", False),
    ("risperidone",     ["Risperdal"],                                  "Atypical Antipsychotic", False),
    ("olanzapine",      ["Zyprexa"],                                    "Atypical Antipsychotic", False),
    ("clozapine",       ["Clozaril", "Fazaclo"],                       "Atypical Antipsychotic", True),
    ("ziprasidone",     ["Geodon"],                                     "Atypical Antipsychotic", False),
    ("lurasidone",      ["Latuda"],                                     "Atypical Antipsychotic", False),
    ("paliperidone",    ["Invega"],                                     "Atypical Antipsychotic", False),
    ("haloperidol",     ["Haldol"],                                     "Typical Antipsychotic", True),
    ("alprazolam",      ["Xanax"],                                      "Benzodiazepine",       True),
    ("lorazepam",       ["Ativan"],                                     "Benzodiazepine",       True),
    ("clonazepam",      ["Klonopin"],                                   "Benzodiazepine",       True),
    ("diazepam",        ["Valium"],                                     "Benzodiazepine",       True),
    ("temazepam",       ["Restoril"],                                   "Benzodiazepine",       True),
    ("zolpidem",        ["Ambien", "Edluar", "Intermezzo"],           "Sedative-Hypnotic",    True),
    ("eszopiclone",     ["Lunesta"],                                    "Sedative-Hypnotic",    True),
    ("zaleplon",        ["Sonata"],                                     "Sedative-Hypnotic",    True),
    ("hydroxyzine",     ["Vistaril", "Atarax"],                       "Antihistamine/Anxiolytic", False),
    ("buspirone",       ["Buspar"],                                     "Anxiolytic",           False),
    ("clonidine",       ["Catapres", "Kapvay"],                        "Alpha-2 Agonist",      False),
    ("guanfacine",      ["Tenex", "Intuniv"],                         "Alpha-2 Agonist",      False),
    ("methylphenidate", ["Ritalin", "Concerta", "Quillivant"],        "Stimulant (ADHD)",     True),
    ("amphetamine",     ["Adderall", "Vyvanse", "Dexedrine"],        "Stimulant (ADHD)",     True),
    ("atomoxetine",     ["Strattera"],                                  "SNRI (ADHD)",          False),
    ("modafinil",       ["Provigil"],                                   "Wakefulness Agent",    False),
    ("baclofen",        ["Lioresal", "Ozobax"],                        "Muscle Relaxant",      False),
    ("cyclobenzaprine", ["Flexeril", "Amrix"],                        "Muscle Relaxant",      False),
    ("tizanidine",      ["Zanaflex"],                                   "Muscle Relaxant",      False),
    ("methocarbamol",   ["Robaxin"],                                    "Muscle Relaxant",      False),
    ("carisoprodol",    ["Soma"],                                       "Muscle Relaxant",      True),
    # ── Endocrine / Thyroid ──────────────────────────────────────────────────
    ("levothyroxine",   ["Synthroid", "Levoxyl", "Tirosint", "Unithroid"], "Thyroid Hormone", False),
    ("liothyronine",    ["Cytomel"],                                    "Thyroid Hormone",      False),
    ("methimazole",     ["Tapazole"],                                   "Antithyroid",          False),
    # ── Antibiotics / Antivirals / Antifungals ───────────────────────────────
    ("amoxicillin",     ["Amoxil", "Trimox"],                          "Penicillin",           False),
    ("amoxicillin-clavulanate", ["Augmentin"],                         "Penicillin + BLI",     False),
    ("azithromycin",    ["Zithromax", "Z-Pack"],                       "Macrolide",            False),
    ("clarithromycin",  ["Biaxin"],                                     "Macrolide",            False),
    ("erythromycin",    ["Ery-Tab", "Erythrocin"],                    "Macrolide",            True),
    ("doxycycline",     ["Vibramycin", "Doryx", "Monodox"],           "Tetracycline",         False),
    ("minocycline",     ["Minocin", "Solodyn"],                        "Tetracycline",         False),
    ("ciprofloxacin",   ["Cipro"],                                      "Fluoroquinolone",      False),
    ("levofloxacin",    ["Levaquin"],                                   "Fluoroquinolone",      False),
    ("cephalexin",      ["Keflex"],                                     "Cephalosporin",        False),
    ("cefdinir",        ["Omnicef"],                                    "Cephalosporin",        False),
    ("ceftriaxone",     ["Rocephin"],                                   "Cephalosporin",        False),
    ("clindamycin",     ["Cleocin"],                                    "Lincosamide",          False),
    ("metronidazole",   ["Flagyl"],                                     "Nitroimidazole",       False),
    ("trimethoprim-sulfamethoxazole", ["Bactrim", "Septra"],          "Sulfonamide + TMP",    False),
    ("nitrofurantoin",  ["Macrobid", "Macrodantin"],                   "Nitrofuran",           False),
    ("vancomycin",      ["Vancocin"],                                   "Glycopeptide",         True),
    ("linezolid",       ["Zyvox"],                                      "Oxazolidinone",        True),
    ("penicillin v",    ["Pen-Vee K", "Veetids"],                     "Penicillin",           False),
    ("fluconazole",     ["Diflucan"],                                   "Antifungal (Azole)",   False),
    ("itraconazole",    ["Sporanox"],                                   "Antifungal (Azole)",   True),
    ("voriconazole",    ["Vfend"],                                      "Antifungal (Azole)",   True),
    ("acyclovir",       ["Zovirax"],                                    "Antiviral (Herpes)",   False),
    ("valacyclovir",    ["Valtrex"],                                    "Antiviral (Herpes)",   False),
    ("oseltamivir",     ["Tamiflu"],                                    "Antiviral (Influenza)", False),
    # ── Rheumatology / Immunology ────────────────────────────────────────────
    ("methotrexate",    ["Rheumatrex", "Trexall", "Otrexup"],        "DMARD",               True),
    ("hydroxychloroquine", ["Plaquenil"],                               "DMARD",               False),
    ("sulfasalazine",   ["Azulfidine"],                                 "DMARD",               False),
    ("leflunomide",     ["Arava"],                                      "DMARD",               True),
    ("adalimumab",      ["Humira"],                                     "Biologic (Anti-TNF)", False),
    ("etanercept",      ["Enbrel"],                                     "Biologic (Anti-TNF)", False),
    ("infliximab",      ["Remicade"],                                   "Biologic (Anti-TNF)", False),
    ("tocilizumab",     ["Actemra"],                                    "Biologic (Anti-IL-6)", False),
    ("baricitinib",     ["Olumiant"],                                   "JAK Inhibitor",        False),
    ("tofacitinib",     ["Xeljanz"],                                    "JAK Inhibitor",        False),
    ("upadacitinib",    ["Rinvoq"],                                     "JAK Inhibitor",        False),
    ("ustekinumab",     ["Stelara"],                                    "Biologic (Anti-IL-12/23)", False),
    ("secukinumab",     ["Cosentyx"],                                   "Biologic (Anti-IL-17)", False),
    ("cyclosporine",    ["Sandimmune", "Neoral", "Restasis"],         "Immunosuppressant",    True),
    ("tacrolimus",      ["Prograf", "Astagraf XL", "Envarsus"],      "Immunosuppressant",    True),
    ("mycophenolate",   ["CellCept", "Myfortic"],                     "Immunosuppressant",    True),
    # ── Urology / Sexual Health ──────────────────────────────────────────────
    ("tamsulosin",      ["Flomax"],                                     "Alpha Blocker",        False),
    ("doxazosin",       ["Cardura"],                                    "Alpha Blocker",        False),
    ("finasteride",     ["Proscar", "Propecia"],                       "5-ARI",               False),
    ("dutasteride",     ["Avodart"],                                    "5-ARI",               False),
    ("sildenafil",      ["Viagra", "Revatio"],                        "PDE-5 Inhibitor",      False),
    ("tadalafil",       ["Cialis", "Adcirca"],                        "PDE-5 Inhibitor",      False),
    ("oxybutynin",      ["Ditropan", "Oxytrol"],                      "Anticholinergic",      False),
    ("tolterodine",     ["Detrol"],                                     "Anticholinergic",      False),
    ("mirabegron",      ["Myrbetriq"],                                  "Beta-3 Agonist",       False),
    # ── Osteoporosis / Bone ──────────────────────────────────────────────────
    ("alendronate",     ["Fosamax"],                                    "Bisphosphonate",       False),
    ("risedronate",     ["Actonel", "Atelvia"],                       "Bisphosphonate",       False),
    ("ibandronate",     ["Boniva"],                                     "Bisphosphonate",       False),
    ("denosumab",       ["Prolia", "Xgeva"],                          "RANKL Inhibitor",      False),
    ("raloxifene",      ["Evista"],                                     "SERM",                False),
    ("teriparatide",    ["Forteo"],                                     "PTH Analog",           False),
    ("romosozumab",     ["Evenity"],                                    "Sclerostin Inhibitor", False),
    # ── Vitamins / Minerals ──────────────────────────────────────────────────
    ("folic acid",      ["Folate", "Folacin"],                        "Vitamin B9",           False),
    ("cholecalciferol", ["Vitamin D3", "D-Vi-Sol"],                   "Vitamin D",            False),
    ("ergocalciferol",  ["Vitamin D2", "Drisdol"],                    "Vitamin D",            False),
    ("calcium carbonate", ["Tums", "Os-Cal", "Caltrate"],            "Calcium Supplement",   False),
    ("calcium citrate", ["Citracal"],                                   "Calcium Supplement",   False),
    ("ferrous sulfate", ["Slow FE", "Fer-In-Sol"],                    "Iron Supplement",      False),
    ("potassium chloride", ["Klor-Con", "K-Dur", "Micro-K"],         "Electrolyte",          False),
    ("magnesium oxide", ["Mag-Ox", "Uro-Mag"],                        "Electrolyte",          False),
    # ── Ophthalmology ────────────────────────────────────────────────────────
    ("latanoprost",     ["Xalatan"],                                    "Prostaglandin Analog (Eye)", False),
    ("timolol",         ["Timoptic"],                                   "Beta Blocker (Eye)",   False),
    ("brimonidine",     ["Alphagan"],                                   "Alpha-2 Agonist (Eye)", False),
    ("dorzolamide",     ["Trusopt"],                                    "Carbonic Anhydrase Inhibitor (Eye)", False),
    # ── Oncology (common orals) ──────────────────────────────────────────────
    ("tamoxifen",       ["Nolvadex", "Soltamox"],                     "SERM (Oncology)",      True),
    ("anastrozole",     ["Arimidex"],                                   "Aromatase Inhibitor",  True),
    ("letrozole",       ["Femara"],                                     "Aromatase Inhibitor",  True),
    ("imatinib",        ["Gleevec"],                                    "TKI",                 True),
    ("ibrutinib",       ["Imbruvica"],                                  "BTK Inhibitor",        True),
    # ── HIV / Antiretrovirals ────────────────────────────────────────────────
    ("emtricitabine-tenofovir", ["Truvada", "Descovy"],               "NRTI Combo",           True),
    ("bictegravir-emtricitabine-tenofovir", ["Biktarvy"],             "NRTI+INSTI",           True),
    ("dolutegravir",    ["Tivicay"],                                    "INSTI",               True),
    ("ritonavir",       ["Norvir", "Paxlovid"],                       "PI/Booster",           True),
    # ── Dermatology ──────────────────────────────────────────────────────────
    ("tretinoin",       ["Retin-A", "Renova", "Atralin"],            "Retinoid",             False),
    ("isotretinoin",    ["Accutane", "Claravis", "Absorica"],        "Retinoid",             True),
    ("clobetasol",      ["Temovate", "Clobex"],                       "Topical Corticosteroid (High Potency)", False),
    ("hydrocortisone",  ["Cortaid", "Cortizone", "Hytone"],          "Topical Corticosteroid", False),
    # ── Migraine ─────────────────────────────────────────────────────────────
    ("sumatriptan",     ["Imitrex", "Zecuity", "Tosymra"],          "Triptan",              False),
    ("rizatriptan",     ["Maxalt"],                                     "Triptan",              False),
    ("ubrogepant",      ["Ubrelvy"],                                    "CGRP Antagonist",      False),
    ("rimegepant",      ["Nurtec ODT"],                                 "CGRP Antagonist",      False),
    ("erenumab",        ["Aimovig"],                                    "CGRP mAb",             False),
    ("fremanezumab",    ["Ajovy"],                                      "CGRP mAb",             False),
    # ── Sleep / OTC Misc ─────────────────────────────────────────────────────
    ("melatonin",       ["Natrol", "ZzzQuil Pure Zzzs"],             "Sleep Aid (OTC)",      False),
    ("doxylamine",      ["Unisom", "NyQuil"],                         "Sleep Aid (OTC)",      False),
    ("zinc",            ["Zicam", "Cold-Eeze"],                       "Mineral Supplement",   False),
    ("vitamin c",       ["Ester-C", "Emergen-C"],                    "Vitamin",              False),
]


# ── Build lookup tables ───────────────────────────────────────────────────────

# canonical_lower → DrugRecord
_CATALOG: dict[str, DrugRecord] = {}

# alias_lower (brand name, generic variant) → canonical_lower
_ALIAS_MAP: dict[str, str] = {}


def _build_tables() -> None:
    seen_canonicals: set[str] = set()
    for canonical, brands, drug_class, high_risk in _SEED:
        key = canonical.lower()
        if key in seen_canonicals:
            # Duplicate canonical (e.g. topiramate listed twice for migraine / AED)
            continue
        seen_canonicals.add(key)
        rec = DrugRecord(
            canonical_name=canonical,
            brand_names=brands,
            drug_class=drug_class,
            is_high_risk=high_risk,
        )
        _CATALOG[key] = rec
        # Register all brand names as aliases
        for brand in brands:
            _ALIAS_MAP[brand.lower()] = key


_build_tables()


# ── Public API ────────────────────────────────────────────────────────────────

def find_drug(name: str) -> Optional[DrugRecord]:
    """
    Find a DrugRecord by any known name (canonical, brand, or RxNorm-normalised).

    Look-up order
    -------------
    1. Exact match on canonical name (case-insensitive)
    2. Brand/alias map
    3. RxNorm API normalisation (network call, cached via lru_cache)
    4. RxNorm approximate / spell-correct (network call, cached)

    Returns None only when no match is found by any method.

    Examples
    --------
    find_drug("Tylenol")    → DrugRecord(canonical_name="acetaminophen", ...)
    find_drug("lipitor")    → DrugRecord(canonical_name="atorvastatin", ...)
    find_drug("ibuprofin")  → DrugRecord(canonical_name="ibuprofen", ...) via spell-correct
    """
    key = (name or "").strip().lower()
    if not key:
        return None

    # 1. Direct catalog match
    if key in _CATALOG:
        return _CATALOG[key]

    # 2. Brand / alias map
    if key in _ALIAS_MAP:
        return _CATALOG.get(_ALIAS_MAP[key])

    # 3. RxNorm normalisation
    try:
        from rxnorm_client import normalize_drug_name
        canonical = normalize_drug_name(name)
        if canonical:
            c_key = canonical.lower()
            if c_key in _CATALOG:
                return _CATALOG[c_key]
            # Not in our catalog but RxNorm knows it — create a minimal record
            logger.info("[DrugCatalog] RxNorm resolved '%s' → '%s' (not in catalog)", name, canonical)
            return DrugRecord(canonical_name=canonical, drug_class="General")
    except Exception as exc:
        logger.debug("[DrugCatalog] RxNorm normalise failed for '%s': %s", name, exc)

    # 4. Approximate / spell-correct
    try:
        from rxnorm_client import spell_correct_drug
        corrected = spell_correct_drug(name)
        if corrected and corrected.lower() != key:
            logger.info("[DrugCatalog] Spell-corrected '%s' → '%s'", name, corrected)
            return find_drug(corrected)  # recurse once with corrected name
    except Exception as exc:
        logger.debug("[DrugCatalog] Spell-correct failed for '%s': %s", name, exc)

    return None


def is_known_drug(name: str) -> bool:
    """Return True if the name resolves to any known drug."""
    return find_drug(name) is not None


def is_high_risk(name: str) -> bool:
    """Return True if the drug is marked as high-risk / narrow therapeutic index."""
    rec = find_drug(name)
    return rec.is_high_risk if rec else False


def get_drug_class(name: str) -> Optional[str]:
    """Return the drug class string for a drug name, or None."""
    rec = find_drug(name)
    return rec.drug_class if rec else None


def catalog_size() -> int:
    """Return the number of canonical entries in the catalog."""
    return len(_CATALOG)


def preload_catalog(force: bool = False) -> dict:
    """
    Warm the RxNorm lru_cache for all top-catalog drugs.

    Makes one RxNorm API call per drug to fetch CUIs and stores canonical names.
    Safe to call at startup in a background thread.

    Parameters
    ----------
    force : bool
        If True, clears the lru_cache before fetching (triggers fresh network calls).

    Returns
    -------
    dict with keys: total, resolved, failed, elapsed_seconds
    """
    import time as _time
    try:
        from rxnorm_client import lookup_rxcui, get_canonical_name
        if force:
            lookup_rxcui.cache_clear()
            get_canonical_name.cache_clear()
    except ImportError:
        logger.warning("[DrugCatalog] rxnorm_client not available — skipping preload")
        return {"total": 0, "resolved": 0, "failed": 0, "elapsed_seconds": 0}

    start = _time.monotonic()
    resolved = 0
    failed = 0
    drugs = list(_CATALOG.keys())

    logger.info("[DrugCatalog] Preloading %d drugs from RxNorm...", len(drugs))
    for drug_name in drugs:
        try:
            rxcui = lookup_rxcui(drug_name)
            if rxcui:
                _CATALOG[drug_name].rxcui = rxcui
                resolved += 1
            else:
                failed += 1
        except Exception as exc:
            logger.debug("[DrugCatalog] Preload failed for '%s': %s", drug_name, exc)
            failed += 1

    elapsed = round(_time.monotonic() - start, 2)
    logger.info(
        "[DrugCatalog] Preload done — %d resolved, %d failed in %.1fs",
        resolved, failed, elapsed,
    )
    return {
        "total":            len(drugs),
        "resolved":         resolved,
        "failed":           failed,
        "elapsed_seconds":  elapsed,
    }
