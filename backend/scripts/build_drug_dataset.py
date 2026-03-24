import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Dict

import requests
import anthropic

logging.basicConfig(level=logging.INFO, format="%(message)s")

FDA_API_URL = "https://api.fda.gov/drug/ndc.json"
LABEL_API_URL = "https://api.fda.gov/drug/label.json"
RXNAV_URL = "https://rxnav.nlm.nih.gov/REST"

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "backend" / "data"
OUTPUT_FILE = DATA_DIR / "rxbuddy_drugs_2000.csv"
CACHE_FILE = DATA_DIR / "claude_cache.json"

# API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

def fetch_top_ndc_drugs(limit: int = 2000) -> List[str]:
    logging.info(f"Fetching top {limit} drugs from OpenFDA NDC directory...")
    # Get generic names that are finished medications and match OTC or Prescription
    # To keep it simple, we filter for finished=true and count the generic names.
    url = f"{FDA_API_URL}?search=finished:true&count=generic_name.exact&limit={limit}"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    return [item["term"].lower() for item in data.get("results", [])]

def fetch_drug_info(generic_name: str) -> Dict:
    details = {
        "canonical_name": generic_name,
        "brand_names": [],
        "generic_names": [generic_name],
        "rxcui": "",
        "drug_class": "General",
        "common_side_effects": [],
        "adverse_reactions": ""
    }
    
    # 1. OpenFDA Label (Adverse Reactions)
    try:
        url = f"{LABEL_API_URL}?search=openfda.generic_name.exact:\"{generic_name}\"&limit=1"
        res = requests.get(url)
        if res.status_code == 200:
            data = res.json()["results"][0]
            adverse = data.get("adverse_reactions", [])
            if adverse:
                details["adverse_reactions"] = adverse[0][:1500] # Cap size
            
            # also grab brand name from openfda
            brands = data.get("openfda", {}).get("brand_name", [])
            if brands:
                details["brand_names"].extend([b.lower() for b in brands])
    except Exception as e:
        logging.warning(f"FDA label fetch failed for {generic_name}: {e}")

    # 2. RxNorm (rxcui & RxClass)
    try:
        url = f"{RXNAV_URL}/rxcui.json?name={generic_name}&search=2"
        res = requests.get(url).json()
        ids = res.get("idGroup", {}).get("rxnormId", [])
        if ids:
            rxcui = ids[0]
            details["rxcui"] = rxcui
            
            # Get therapeutic class
            class_url = f"{RXNAV_URL}/rxclass/class/byRxcui.json?rxcui={rxcui}&relaSource=DAILYMED"
            class_res = requests.get(class_url)
            if class_res.status_code == 200:
                class_data = class_res.json()
                classes = class_data.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", [])
                if classes:
                    details["drug_class"] = classes[0]["rxclassMinConceptItem"]["className"]
    except Exception as e:
        logging.warning(f"RxNorm fetch failed for {generic_name}: {e}")
        
    details["brand_names"] = list(set(details["brand_names"]))
    return details


def load_cache() -> Dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache_data: Dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)


def batch_call_claude(drugs: List[Dict], cache: Dict) -> List[Dict]:
    if not ANTHROPIC_API_KEY:
        logging.warning("No Anthropic API key found, skipping Claude generation for mechanism/use.")
        return drugs
        
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    uncached_drugs = []
    for d in drugs:
        name = d["canonical_name"]
        if name in cache:
            d["mechanism_simple"] = cache[name].get("mechanism_simple", "")
            d["common_use"] = cache[name].get("common_use", "")
        else:
            uncached_drugs.append(d)
            
    if not uncached_drugs:
        return drugs

    logging.info(f"Calling Claude for {len(uncached_drugs)} drugs...")
    
    prompt = "For each of the following drug generic names, provide a 1-sentence 'common_use' and a 1-sentence 'mechanism_simple'. Return ONLY valid JSON format where keys are drug names.\n\nDrugs:\n"
    for d in uncached_drugs:
        prompt += f"- {d['canonical_name']}\n"
        
    prompt += "\nFormat exactly like this:\n{\n\"drug_name1\": {\"common_use\": \"...\", \"mechanism_simple\": \"...\"}\n}"

    try:
        response = client.messages.create(
            model="PLACEHOLDER_M37",
            max_tokens=2048,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )
        
        text = response.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        result_json = json.loads(text[start:end])
        
        for d in uncached_drugs:
            name = d["canonical_name"]
            info = result_json.get(name, {})
            d["mechanism_simple"] = info.get("mechanism_simple", "Mechanism not available.")
            d["common_use"] = info.get("common_use", "Uses not available.")
            
            cache[name] = {"mechanism_simple": d["mechanism_simple"], "common_use": d["common_use"]}
            
        save_cache(cache)
        
    except Exception as e:
        logging.error(f"Claude API failed: {e}")
        
    time.sleep(1)
    return drugs

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    drugs_list = fetch_top_ndc_drugs(2000)
    logging.info(f"Found {len(drugs_list)} drugs. Processing in batches...")
    
    all_drug_data = []
    cache = load_cache()
    
    batch_size = 10
    total_drugs = len(drugs_list)
    for i in range(0, total_drugs, batch_size):
        batch_names = drugs_list[i:i+batch_size]
        logging.info(f"Processing batch {i//batch_size + 1}/{(total_drugs + batch_size - 1)//batch_size}...")
        
        batch_data = []
        for name in batch_names:
            info = fetch_drug_info(name)
            
            # Extract basic side effects from raw adverse reactions if possible
            if info["adverse_reactions"]:
                # simple split by comma or semicolon
                text = info["adverse_reactions"].replace(";", ",")
                parts = [p.strip() for p in text.split(",") if 3 < len(p.strip()) < 50]
                info["common_side_effects"] = parts[:5]
            
            batch_data.append(info)
            time.sleep(0.5) # FDA / NLM rate limiting
            
        batch_data = batch_call_claude(batch_data, cache)
        all_drug_data.extend(batch_data)
        
        # Save incrementally
        fields = [
            "canonical_name", "brand_names", "generic_names", "rxcui",
            "drug_class", "common_use", "mechanism_simple",
            "common_side_effects", "serious_side_effects", "adverse_reactions"
        ]
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for d in all_drug_data:
                row = d.copy()
                row["brand_names"] = ";".join(d["brand_names"])
                row["generic_names"] = ";".join(d["generic_names"])
                row["common_side_effects"] = ";".join(d.get("common_side_effects", []))
                row["serious_side_effects"] = ""
                writer.writerow(row)
                
    logging.info(f"Saved dataset to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
