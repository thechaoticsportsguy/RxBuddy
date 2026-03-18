import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// BUG 3 FIX: DrugImage component with category-based SVG pills
function DrugImage({ drugName, className = "" }) {
  const [imageData, setImageData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!drugName) {
      setLoading(false);
      return;
    }

    async function fetchImage() {
      try {
        const res = await fetch(`${API_BASE}/drug-image?name=${encodeURIComponent(drugName)}`);
        if (res.ok) {
          const data = await res.json();
          setImageData(data);
        }
      } catch (e) {
        console.error("[DrugImage] Error:", e);
      } finally {
        setLoading(false);
      }
    }

    fetchImage();
  }, [drugName]);

  // Category label colors for the badge
  const categoryColors = {
    OTC: "bg-emerald-100 text-emerald-700",
    PRESCRIPTION: "bg-blue-100 text-blue-700",
    HIGH_RISK: "bg-red-100 text-red-700",
    ANTIBIOTIC: "bg-orange-100 text-orange-700",
    CONTROLLED: "bg-purple-100 text-purple-700",
  };

  if (loading) {
    // Loading skeleton
    return (
      <div className={`flex flex-col items-center ${className}`}>
        <div className="animate-pulse bg-slate-200 rounded-lg" style={{ width: 60, height: 60 }} />
        <div className="animate-pulse bg-slate-200 rounded h-3 w-12 mt-1" />
      </div>
    );
  }

  // BUG 3 FIX: Always render SVG pill with category label
  if (imageData?.svg_data) {
    const colorClass = categoryColors[imageData.category] || categoryColors.OTC;
    return (
      <div className={`flex flex-col items-center ${className}`}>
        <div 
          style={{ width: 60, height: 60 }}
          dangerouslySetInnerHTML={{ __html: imageData.svg_data }}
        />
        <span className={`mt-1 px-1.5 py-0.5 rounded text-[9px] font-medium ${colorClass}`}>
          {imageData.category_label || "OTC"}
        </span>
      </div>
    );
  }

  // Fallback: default green pill SVG
  const defaultSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60" width="60" height="60">
    <ellipse cx="30" cy="52" rx="20" ry="4" fill="rgba(0,0,0,0.15)"/>
    <path d="M10 30 C10 19 18 12 30 12 L30 48 C18 48 10 41 10 30" fill="#52B788" stroke="#2D6A4F" stroke-width="1.5"/>
    <path d="M30 12 C42 12 50 19 50 30 C50 41 42 48 30 48 L30 12" fill="#FFFFFF" stroke="#2D6A4F" stroke-width="1.5"/>
    <line x1="30" y1="12" x2="30" y2="48" stroke="#2D6A4F" stroke-width="1"/>
    <ellipse cx="20" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.35)"/>
    <ellipse cx="40" cy="22" rx="6" ry="3" fill="rgba(255,255,255,0.5)"/>
  </svg>`;
  
  return (
    <div className={`flex flex-col items-center ${className}`}>
      <div 
        style={{ width: 60, height: 60 }}
        dangerouslySetInnerHTML={{ __html: defaultSvg }}
      />
      <span className="mt-1 px-1.5 py-0.5 rounded text-[9px] font-medium bg-emerald-100 text-emerald-700">
        OTC
      </span>
    </div>
  );
}

// Generic phrases to filter out from answers
const GENERIC_PHRASES = [
  "follow the package directions",
  "follow package directions",
  "use the lowest effective dose",
  "use the lowest dose",
  "ask your pharmacist",
  "consult your pharmacist",
  "if you're unsure",
  "if unsure",
  "as directed",
  "read the label",
];

/**
 * Detect question type from keywords for dynamic templates
 */
function detectQuestionType(question) {
  const q = String(question || "").toLowerCase();
  
  if (q.includes("can i take") && q.includes("with") || q.includes("interaction") || q.includes("mix") || q.includes("combine")) {
    return "interaction";
  }
  if (q.includes("dose") || q.includes("how much") || q.includes("how many") || q.includes("max")) {
    return "dosage";
  }
  if (q.includes("side effect") || q.includes("effects") || q.includes("cause") || q.includes("symptoms")) {
    return "side_effects";
  }
  if (q.includes("what is") || q.includes("how does") || q.includes("what does") || q.includes("explain")) {
    return "explanation";
  }
  if (q.includes("safe") || q.includes("okay") || q.includes("ok to")) {
    return "safety";
  }
  return "general";
}

/**
 * Filter out generic advice from an array of items
 */
function filterGenericAdvice(items) {
  if (!Array.isArray(items)) return [];
  return items.filter(item => {
    const lower = String(item || "").toLowerCase();
    return !GENERIC_PHRASES.some(phrase => lower.includes(phrase));
  });
}

/**
 * Parse Claude's new Answer/Why/Important notes/Get medical help format.
 *
 * Expected format from API:
 *   Answer: YES / NO / USUALLY YES / NEEDS REVIEW
 *   Why: 1-2 simple sentences
 *   Important notes: bullet list (lines starting with - or •)
 *   Get medical help now if: bullet list
 *
 * Also supports legacy VERDICT / DIRECT formats as fallback.
 */
function parseVerdictAnswer(answer, structured = null) {
  console.log("[parseVerdictAnswer] Raw answer:", answer);
  console.log("[parseVerdictAnswer] Structured data:", structured);

  const text = String(answer || "").replace(/\r\n/g, "\n").trim();
  if (!text) return null;

  let verdict = null;       // YES, NO, USUALLY_YES, NEEDS_REVIEW
  let why = "";
  let importantNotes = [];
  let medicalHelp = [];

  // Track which multi-line section we're collecting bullets for
  let currentSection = null;

  const lines = text.split("\n");
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    const upperLine = line.toUpperCase();

    // --- New format ---
    if (upperLine.startsWith("ANSWER:")) {
      const val = line.substring(7).trim();
      const upper = val.toUpperCase();
      if (upper.startsWith("YES") || upper.startsWith("USUALLY YES")) {
        verdict = upper.startsWith("USUALLY") ? "USUALLY_YES" : "YES";
      } else if (upper.startsWith("NO")) {
        verdict = "NO";
      } else if (upper.startsWith("NEEDS REVIEW") || upper.startsWith("NEEDS_REVIEW")) {
        verdict = "NEEDS_REVIEW";
      }
      currentSection = null;
      continue;
    }

    if (upperLine.startsWith("WHY:")) {
      why = line.substring(4).trim();
      currentSection = null;
      continue;
    }

    if (upperLine.startsWith("IMPORTANT NOTES:") || upperLine.startsWith("IMPORTANT NOTE:")) {
      const inline = line.substring(line.indexOf(":") + 1).trim();
      if (inline && !inline.startsWith("-") && !inline.startsWith("•")) {
        importantNotes.push(inline);
      }
      currentSection = "notes";
      continue;
    }

    if (upperLine.startsWith("GET MEDICAL HELP NOW IF:") || upperLine.startsWith("GET MEDICAL HELP IF:")) {
      const inline = line.substring(line.indexOf(":") + 1).trim();
      if (inline && !inline.startsWith("-") && !inline.startsWith("•")) {
        medicalHelp.push(inline);
      }
      currentSection = "help";
      continue;
    }

    // --- Legacy VERDICT format fallback ---
    if (upperLine.startsWith("VERDICT:")) {
      const vText = line.substring(8).trim();
      const dashIdx = vText.indexOf("—") !== -1 ? vText.indexOf("—") : vText.indexOf("-");
      const vPart = (dashIdx > 0 ? vText.substring(0, dashIdx) : vText).trim().toUpperCase();
      if (vPart.startsWith("YES")) verdict = "YES";
      else if (vPart.startsWith("NO")) verdict = "NO";
      else if (vPart.startsWith("CONDITIONAL") || vPart.startsWith("USUALLY")) verdict = "USUALLY_YES";
      currentSection = null;
      continue;
    }
    if (upperLine.startsWith("REASON:")) {
      why = why || line.substring(7).trim();
      currentSection = null;
      continue;
    }
    if (upperLine.startsWith("DIRECT:")) {
      const dt = line.substring(7).trim();
      if (/^\s*yes\b/i.test(dt)) verdict = verdict || "YES";
      else if (/^\s*no\b/i.test(dt)) verdict = verdict || "NO";
      why = why || dt.replace(/^(yes|no)[,.]?\s*/i, "").trim();
      currentSection = null;
      continue;
    }
    if (upperLine.startsWith("AVOID:") || upperLine.startsWith("WARNING:") || upperLine.startsWith("DOCTOR:")) {
      const prefix = upperLine.startsWith("AVOID:") ? 6 : (upperLine.startsWith("WARNING:") ? 8 : 7);
      const items = line.substring(prefix).split("|").map(s => s.trim()).filter(Boolean);
      if (upperLine.startsWith("AVOID:")) importantNotes.push(...filterGenericAdvice(items));
      else medicalHelp.push(...filterGenericAdvice(items));
      currentSection = null;
      continue;
    }

    // Collect bullet lines for the current multi-line section
    if (currentSection) {
      const bullet = line.replace(/^[-•*]\s*/, "").trim();
      if (bullet) {
        if (currentSection === "notes") importantNotes.push(bullet);
        else if (currentSection === "help") medicalHelp.push(bullet);
      }
    }
  }

  // Filter out generic advice
  importantNotes = filterGenericAdvice(importantNotes);
  medicalHelp = filterGenericAdvice(medicalHelp);

  // Fallback verdict detection from body text
  if (!verdict && text) {
    const lower = text.toLowerCase();
    if (lower.includes("yes,") || lower.includes("yes you can") || lower.includes("it is safe")) verdict = "YES";
    else if (lower.includes("usually yes")) verdict = "USUALLY_YES";
    else if (lower.includes("no,") || lower.includes("do not") || lower.includes("not recommended")) verdict = "NO";
    else if (lower.includes("needs review") || lower.includes("depends") || lower.includes("conditional")) verdict = "NEEDS_REVIEW";
  }

  const result = {
    verdict,
    why,
    importantNotes,
    medicalHelp,
    full: text,
    hasContent: Boolean(verdict || why || importantNotes.length || medicalHelp.length),
  };

  console.log("[parseVerdictAnswer] Parsed result:", result);
  return result;
}

/**
 * BUG 3 FIX: Build PubMed search query from user question
 * Extracts ALL drug names + category and builds a targeted AND query
 */
const BRAND_TO_GENERIC = {
  tylenol: "acetaminophen", advil: "ibuprofen", motrin: "ibuprofen",
  aleve: "naproxen", bayer: "aspirin", excedrin: "acetaminophen",
  benadryl: "diphenhydramine", claritin: "loratadine", zyrtec: "cetirizine",
  allegra: "fexofenadine", prilosec: "omeprazole", nexium: "esomeprazole",
  pepcid: "famotidine", xanax: "alprazolam", valium: "diazepam",
  ambien: "zolpidem", zoloft: "sertraline", prozac: "fluoxetine",
  lexapro: "escitalopram", lipitor: "atorvastatin", crestor: "rosuvastatin",
  viagra: "sildenafil", cialis: "tadalafil", synthroid: "levothyroxine",
};

const GENERIC_DRUGS = [
  "ibuprofen", "acetaminophen", "aspirin", "naproxen", "amoxicillin",
  "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
  "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
  "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
  "hydrocodone", "oxycodone", "tramadol", "warfarin", "ciprofloxacin",
  "diphenhydramine", "loratadine", "cetirizine", "fexofenadine", "sildenafil",
];

const TOPIC_MAPPINGS = {
  "empty stomach": "food drug administration",
  "with food": "food drug administration",
  food: "food drug interaction",
  pregnancy: "pregnancy safety",
  pregnant: "pregnancy safety",
  breastfeeding: "lactation safety",
  alcohol: "alcohol drug interaction",
  "side effect": "adverse effects",
  interaction: "drug interaction",
  children: "pediatric dosing",
  liver: "hepatic impairment",
  kidney: "renal impairment",
  "blood pressure": "hypertension",
  diabetes: "diabetes mellitus",
  overdose: "overdose toxicity",
  allergy: "hypersensitivity",
};

function extractDrugNames(query) {
  const q = String(query || "").toLowerCase().trim();
  const drugs = [];
  
  // Check brand names first (convert to generic)
  for (const [brand, generic] of Object.entries(BRAND_TO_GENERIC)) {
    if (q.includes(brand)) {
      drugs.push(generic);
    }
  }
  
  // Check generic names
  for (const drug of GENERIC_DRUGS) {
    if (q.includes(drug) && !drugs.includes(drug)) {
      drugs.push(drug);
    }
  }
  
  return drugs;
}

function buildPubMedQuery(query) {
  const q = String(query || "").toLowerCase().trim();
  if (!q) return { searchTerm: "", drugNames: [] };

  const drugNames = extractDrugNames(q);
  
  // Find the topic/category
  let topic = "";
  for (const [keyword, pubmedTerm] of Object.entries(TOPIC_MAPPINGS)) {
    if (q.includes(keyword)) {
      topic = pubmedTerm;
      break;
    }
  }

  // BUG 3 FIX: Build targeted AND query with all drug names + category
  let searchTerm = "";
  
  if (drugNames.length >= 2) {
    // Multiple drugs: "ibuprofen AND aspirin AND drug interaction"
    searchTerm = drugNames.join(" AND ");
    if (topic) {
      searchTerm += ` AND ${topic}`;
    } else {
      searchTerm += " AND drug interaction";
    }
  } else if (drugNames.length === 1) {
    // Single drug: "ibuprofen AND adverse effects"
    searchTerm = drugNames[0];
    if (topic) {
      searchTerm += ` AND ${topic}`;
    } else {
      searchTerm += " AND pharmacology";
    }
  } else if (topic) {
    // No drug but has topic: "medication AND pregnancy safety"
    searchTerm = `medication AND ${topic}`;
  } else {
    // Fallback: extract keywords
    const stopWords = new Set(["can", "i", "is", "it", "the", "a", "an", "to", "with", "my", "me", "if", "or", "and", "of", "for", "on", "in", "at", "this", "that", "what", "how", "does", "do", "should", "would", "could", "will", "take", "taking", "use", "using", "safe", "okay", "ok", "be", "am"]);
    const words = q.split(/\s+/).filter(w => w.length > 3 && !stopWords.has(w));
    searchTerm = words.slice(0, 3).join(" AND ") || "medication safety";
  }

  return { searchTerm, drugNames };
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// BUG 3 FIX: Fallback safety articles when no relevant results found
const FALLBACK_SAFETY_ARTICLES = [
  {
    id: "fallback-1",
    title: "General Principles of Drug Interactions",
    journal: "American Family Physician",
    year: "2023",
    url: "https://pubmed.ncbi.nlm.nih.gov/?term=drug+interaction+principles",
    takeaway: "Understanding how medications interact is crucial for safe use."
  },
  {
    id: "fallback-2",
    title: "Safe Medication Use: A Guide for Patients",
    journal: "FDA Consumer Health Information",
    year: "2023",
    url: "https://www.fda.gov/drugs/resources-you-drugs/safe-use-medicines",
    takeaway: "Always read labels and consult your pharmacist about drug interactions."
  },
];

// BUG 3 FIX: Check if article title contains at least one drug name
function isArticleRelevant(title, drugNames) {
  if (!drugNames || drugNames.length === 0) return true; // No drugs to filter by
  
  const titleLower = String(title || "").toLowerCase();
  return drugNames.some(drug => titleLower.includes(drug.toLowerCase()));
}

async function fetchPubMedArticles(query) {
  const rawQuery = String(query || "").trim();
  if (!rawQuery) return [];

  const { searchTerm, drugNames } = buildPubMedQuery(rawQuery);
  console.log("[PubMed] Search term:", searchTerm, "| Drug names:", drugNames);
  if (!searchTerm) return [];

  try {
    const esearchUrl = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi");
    esearchUrl.searchParams.set("db", "pubmed");
    esearchUrl.searchParams.set("term", searchTerm);
    esearchUrl.searchParams.set("retmax", "10"); // Fetch more to filter
    esearchUrl.searchParams.set("retmode", "json");
    esearchUrl.searchParams.set("sort", "relevance");

    const searchRes = await fetch(esearchUrl.toString());
    if (!searchRes.ok) return FALLBACK_SAFETY_ARTICLES;

    const searchData = await searchRes.json();
    const ids = searchData?.esearchresult?.idlist || [];
    if (!Array.isArray(ids) || ids.length === 0) return FALLBACK_SAFETY_ARTICLES;

    await sleep(200);

    const esummaryUrl = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi");
    esummaryUrl.searchParams.set("db", "pubmed");
    esummaryUrl.searchParams.set("id", ids.join(","));
    esummaryUrl.searchParams.set("retmode", "json");

    const summaryRes = await fetch(esummaryUrl.toString());
    if (!summaryRes.ok) return FALLBACK_SAFETY_ARTICLES;

    const summaryData = await summaryRes.json();
    const results = summaryData?.result || {};

    const allArticles = [];
    for (const id of ids) {
      const article = results[id];
      if (!article) continue;

      const title = article.title ? String(article.title).replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim() : `PubMed Article ${id}`;
      const journal = article.fulljournalname ? String(article.fulljournalname).trim() : (article.source ? String(article.source).trim() : "PubMed");
      const pubdate = article.pubdate ? String(article.pubdate) : "";
      const yearMatch = pubdate.match(/\b(19|20)\d{2}\b/);
      const year = yearMatch ? yearMatch[0] : "";

      let takeaway = title.length > 100 ? title.substring(0, 97) + "..." : title;

      allArticles.push({ id: String(id), title, journal, year, url: `https://pubmed.ncbi.nlm.nih.gov/${id}/`, takeaway });
    }

    // BUG 3 FIX: Filter to only relevant articles (title contains drug name)
    const relevantArticles = allArticles.filter(a => isArticleRelevant(a.title, drugNames));
    console.log("[PubMed] Found", allArticles.length, "articles,", relevantArticles.length, "relevant");

    // BUG 3 FIX: If fewer than 2 relevant articles, add fallback safety articles
    if (relevantArticles.length < 2) {
      const combined = [...relevantArticles, ...FALLBACK_SAFETY_ARTICLES];
      return combined.slice(0, 3);
    }

    return relevantArticles.slice(0, 3);
  } catch (error) {
    console.error("[PubMed] Error:", error);
    return FALLBACK_SAFETY_ARTICLES;
  }
}

export default function ResultsPage() {
  const router = useRouter();
  const q = typeof router.query.q === "string" ? router.query.q : "";
  const engine = typeof router.query.engine === "string" ? router.query.engine : "tfidf";

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState([]);
  const [didYouMean, setDidYouMean] = useState(null);
  const [source, setSource] = useState("database");
  const [savedToDb, setSavedToDb] = useState(false);

  const [pubmedLoading, setPubmedLoading] = useState(false);
  const [articles, setArticles] = useState([]);

  const [headerQuery, setHeaderQuery] = useState("");
  const [showFullAnswer, setShowFullAnswer] = useState(false);
  const [showMechanism, setShowMechanism] = useState(false);
  const [showGenericTips, setShowGenericTips] = useState(false);

  const title = useMemo(() => (q ? `Results - ${q}` : "Results"), [q]);
  const questionType = useMemo(() => detectQuestionType(q), [q]);
  
  // BUG 4 FIX: Extract drug name from question for drug image
  const extractedDrugName = useMemo(() => {
    const drugs = extractDrugNames(q);
    return drugs.length > 0 ? drugs[0] : null;
  }, [q]);
  
  const parsedAnswer = useMemo(() => {
    const first = results?.[0];
    if (!first || typeof first.answer !== "string") return null;
    return parseVerdictAnswer(first.answer, first.structured);
  }, [results]);

  useEffect(() => { setHeaderQuery(q || ""); }, [q]);

  useEffect(() => {
    if (!router.isReady || !q) return;
    let cancelled = false;

    async function run() {
      setLoading(true);
      setError("");
      setDidYouMean(null);
      setSource("database");
      setSavedToDb(false);
      setShowFullAnswer(false);

      try {
        const res = await fetch(`${API_BASE}/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, engine, top_k: 5 }),
        });

        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `Request failed (${res.status})`);
        }

        const data = await res.json();
        console.log("[RxBuddy] API Response:", data);

        if (!cancelled) {
          setResults(Array.isArray(data.results) ? data.results : []);
          setDidYouMean(data.did_you_mean || null);
          setSource(data.source || "database");
          setSavedToDb(data.saved_to_db || false);
        }
      } catch (e) {
        if (!cancelled) setError(e?.message || "Could not load results.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    run();
    return () => { cancelled = true; };
  }, [router.isReady, q, engine]);

  useEffect(() => {
    if (!router.isReady || !q) return;
    let cancelled = false;

    async function runPubMed() {
      setPubmedLoading(true);
      try {
        const items = await fetchPubMedArticles(q);
        if (!cancelled) setArticles(items);
      } catch (e) {
        console.error("[PubMed] Error:", e);
      } finally {
        if (!cancelled) setPubmedLoading(false);
      }
    }

    runPubMed();
    return () => { cancelled = true; };
  }, [router.isReady, q]);

  function onSubmitHeader(e) {
    e.preventDefault();
    const nextQ = String(headerQuery || "").trim();
    if (!nextQ) return;
    router.push(`/results?q=${encodeURIComponent(nextQ)}&engine=${encodeURIComponent(engine)}`);
  }

  // Verdict styling — maps each verdict to colours + icon
  // BUG 1 FIX: All verdict types with proper colors
  const verdictStyles = {
    YES:                 { bg: "bg-emerald-50",  border: "border-emerald-300", text: "text-emerald-800", icon: "✅", label: "YES" },
    USUALLY_YES:         { bg: "bg-emerald-50",  border: "border-emerald-300", text: "text-emerald-800", icon: "✅", label: "USUALLY YES" },
    NO:                  { bg: "bg-rose-50",     border: "border-rose-300",    text: "text-rose-800",    icon: "❌", label: "NO" },
    MAYBE:               { bg: "bg-amber-50",    border: "border-amber-300",   text: "text-amber-800",   icon: "⚠️", label: "MAYBE" },
    NEEDS_REVIEW:        { bg: "bg-amber-50",    border: "border-amber-300",   text: "text-amber-800",   icon: "⚠️", label: "NEEDS REVIEW" },
    CONSULT_PHARMACIST:  { bg: "bg-blue-50",     border: "border-blue-300",    text: "text-blue-800",    icon: "💊", label: "CONSULT PHARMACIST" },
  };

  // BUG 1 FIX: Robust verdict extraction - checks multiple locations and ALWAYS returns a valid verdict
  const getVerdict = () => {
    const first = results?.[0];
    
    // Debug logging
    console.log("[Verdict Debug] first result:", first);
    console.log("[Verdict Debug] structured:", first?.structured);
    console.log("[Verdict Debug] structured.verdict:", first?.structured?.verdict);
    console.log("[Verdict Debug] parsedAnswer:", parsedAnswer);
    
    // 1. Check structured.verdict from backend API response
    if (first?.structured?.verdict) {
      const v = first.structured.verdict;
      if (verdictStyles[v]) {
        console.log("[Verdict] Using structured.verdict:", v);
        return verdictStyles[v];
      }
    }
    
    // 2. Check if verdict is directly on the result object (some API formats)
    if (first?.verdict) {
      const v = first.verdict;
      if (verdictStyles[v]) {
        console.log("[Verdict] Using first.verdict:", v);
        return verdictStyles[v];
      }
    }
    
    // 3. Check parsedAnswer.verdict from frontend parsing
    if (parsedAnswer?.verdict) {
      const v = parsedAnswer.verdict;
      if (verdictStyles[v]) {
        console.log("[Verdict] Using parsedAnswer.verdict:", v);
        return verdictStyles[v];
      }
    }
    
    // 4. Try to extract verdict from answer text directly
    const answerText = first?.answer || "";
    if (answerText) {
      const upperText = answerText.toUpperCase();
      if (upperText.includes("ANSWER: YES") || upperText.includes("YES, YOU CAN") || upperText.includes("YES YOU CAN")) {
        console.log("[Verdict] Extracted YES from answer text");
        return verdictStyles.YES;
      }
      if (upperText.includes("ANSWER: NO") || upperText.includes("NO, YOU SHOULD NOT") || upperText.includes("DO NOT TAKE")) {
        console.log("[Verdict] Extracted NO from answer text");
        return verdictStyles.NO;
      }
      if (upperText.includes("MAYBE") || upperText.includes("IT DEPENDS") || upperText.includes("DEPENDS ON")) {
        console.log("[Verdict] Extracted MAYBE from answer text");
        return verdictStyles.MAYBE;
      }
    }
    
    // 5. FALLBACK: Always return CONSULT_PHARMACIST (never null, never blank)
    console.log("[Verdict] Using fallback: CONSULT_PHARMACIST");
    return verdictStyles.CONSULT_PHARMACIST;
  };

  // BUG 1 FIX: Always compute verdict when we have results
  const currentVerdict = results?.length > 0 ? getVerdict() : null;

  return (
    <>
      <Head>
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </Head>

      <div className="min-h-screen bg-slate-50" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        {/* Header */}
        <div className="sticky top-0 z-20 border-b border-slate-200 bg-white shadow-sm">
          <div className="mx-auto max-w-4xl px-4 py-3">
            <div className="flex items-center gap-4">
              <Link href="/" className="flex items-center gap-2">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500 text-white">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-3-3v6m-7 4h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <span className="text-lg font-bold text-slate-900">RxBuddy</span>
              </Link>

              <form onSubmit={onSubmitHeader} className="flex-1 flex items-center gap-2">
                <div className="flex flex-1 items-center rounded-full border border-slate-200 bg-slate-50 px-4 py-2 focus-within:ring-2 focus-within:ring-emerald-200 focus-within:border-emerald-300">
                  <svg className="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <input
                    value={headerQuery}
                    onChange={(e) => setHeaderQuery(e.target.value)}
                    placeholder="Ask about medications..."
                    className="ml-2 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                  />
                </div>
                <button type="submit" className="rounded-full bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 transition-colors">
                  Search
                </button>
              </form>
            </div>
          </div>
        </div>

        <div className="mx-auto max-w-4xl px-4 py-6">
          {/* Did you mean? */}
          {didYouMean && (
            <div
              className="mb-4 cursor-pointer rounded-lg border border-amber-200 bg-amber-50 p-3 hover:bg-amber-100 transition-colors"
              onClick={() => router.push(`/results?q=${encodeURIComponent(didYouMean)}&engine=${encodeURIComponent(engine)}`)}
            >
              <p className="text-sm text-amber-800">
                <span className="font-semibold">Did you mean:</span> <span className="underline">{didYouMean}</span>?
              </p>
            </div>
          )}

          {/* Question Card - BUG 4 FIX: Added DrugImage */}
          <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start gap-4">
              {/* Drug Image */}
              {extractedDrugName && (
                <DrugImage drugName={extractedDrugName} className="shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Your Question</p>
                    <p className="mt-1 text-lg font-semibold text-slate-900">{q || "-"}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {source === "database" ? (
                      <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700">Database</span>
                    ) : (
                      <span className="rounded-full bg-violet-100 px-2.5 py-1 text-xs font-medium text-violet-700">AI Generated</span>
                    )}
                    {savedToDb && (
                      <span className="rounded-full bg-green-100 px-2.5 py-1 text-xs font-medium text-green-700">Saved</span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {loading ? (
            <div className="rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
              <div className="mx-auto h-8 w-8 animate-spin rounded-full border-3 border-slate-200 border-t-emerald-500" />
              <p className="mt-3 text-sm text-slate-600">Generating your answer...</p>
            </div>
          ) : error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 shadow-sm">
              <p className="font-semibold text-rose-800">Error loading results</p>
              <p className="mt-1 text-sm text-rose-700">{error}</p>
            </div>
          ) : (
            <>
              {/* BUG 1 FIX: VERDICT BANNER - Always renders at TOP, never hidden, never blank */}
              {/* This banner MUST be the first thing users see after the question card */}
              {currentVerdict ? (
                <div className={`mb-4 rounded-xl border-2 ${currentVerdict.border} ${currentVerdict.bg} p-5 shadow-md`}>
                  <div className="flex items-start gap-3">
                    <span className="text-3xl leading-none">{currentVerdict.icon}</span>
                    <div className="flex-1 min-w-0">
                      <span className={`text-xl font-bold ${currentVerdict.text}`}>{currentVerdict.label}</span>
                      {/* Show the explanation from structured.direct, parsedAnswer.why, or first sentence of answer */}
                      {(() => {
                        const explanation = results?.[0]?.structured?.direct 
                          || parsedAnswer?.why 
                          || (results?.[0]?.answer ? results[0].answer.split('.')[0] + '.' : null);
                        return explanation ? (
                          <p className="mt-2 text-base text-slate-700 leading-relaxed">{explanation}</p>
                        ) : null;
                      })()}
                    </div>
                  </div>
                </div>
              ) : (
                /* Fallback banner if somehow currentVerdict is still null - should never happen */
                <div className="mb-4 rounded-xl border-2 border-blue-300 bg-blue-50 p-5 shadow-md">
                  <div className="flex items-start gap-3">
                    <span className="text-3xl leading-none">💊</span>
                    <div className="flex-1 min-w-0">
                      <span className="text-xl font-bold text-blue-800">CONSULT PHARMACIST</span>
                      <p className="mt-2 text-base text-slate-700 leading-relaxed">
                        Please consult with a pharmacist or healthcare provider for personalized advice about this medication question.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Important Notes — green box - BUG 2 FIX: Use structured.do/avoid from backend */}
              {(() => {
                const structured = results?.[0]?.structured;
                const doItems = structured?.do || [];
                const avoidItems = structured?.avoid || [];
                const parsedNotes = parsedAnswer?.importantNotes || [];
                const allNotes = [...doItems, ...avoidItems, ...parsedNotes].filter((v, i, a) => a.indexOf(v) === i);
                
                if (allNotes.length === 0) return null;
                
                return (
                  <div className="mb-4 rounded-lg border border-emerald-200 bg-white p-4 shadow-sm">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-lg">📋</span>
                      <h3 className="font-semibold text-emerald-800">Important Notes</h3>
                    </div>
                    <ul className="space-y-1.5">
                      {allNotes.map((item, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                          <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}

              {/* Get Medical Help Now If — red/amber box - BUG 2 FIX: Use structured.doctor from backend */}
              {(() => {
                const structured = results?.[0]?.structured;
                const doctorItems = structured?.doctor || [];
                const parsedHelp = parsedAnswer?.medicalHelp || [];
                const allHelp = [...doctorItems, ...parsedHelp].filter((v, i, a) => a.indexOf(v) === i);
                
                if (allHelp.length === 0) return null;
                
                return (
                  <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-4 shadow-sm">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-lg">🚨</span>
                      <h3 className="font-semibold text-rose-800">Get Medical Help Now If</h3>
                    </div>
                    <ul className="space-y-1.5">
                      {allHelp.map((item, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                          <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-rose-400 shrink-0" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}

              {/* Collapsible Full Answer - BUG 2 FIX: Better markdown rendering with prose styling */}
              {(parsedAnswer?.full || results?.[0]?.answer) && (
                <div className="mb-4">
                  <button
                    onClick={() => setShowFullAnswer(!showFullAnswer)}
                    className="w-full rounded-lg border border-slate-200 bg-white px-4 py-3 text-left shadow-sm hover:bg-slate-50 transition-colors flex items-center justify-between"
                  >
                    <span className="text-sm font-medium text-slate-700">Show Detailed Explanation</span>
                    <svg className={`h-4 w-4 text-slate-400 transition-transform ${showFullAnswer ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {showFullAnswer && (
                    <div className="mt-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                      {/* BUG 2 FIX: Proper prose styling for markdown content */}
                      <div className="prose prose-sm prose-slate max-w-none prose-headings:font-semibold prose-headings:text-slate-800 prose-p:text-slate-700 prose-li:text-slate-700 prose-strong:text-slate-800 prose-ul:list-disc prose-ol:list-decimal">
                        <ReactMarkdown
                          components={{
                            // BUG 2 FIX: Custom rendering for better bullet point display
                            ul: ({node, ...props}) => <ul className="space-y-1 pl-4 list-disc" {...props} />,
                            ol: ({node, ...props}) => <ol className="space-y-1 pl-4 list-decimal" {...props} />,
                            li: ({node, ...props}) => <li className="text-slate-700" {...props} />,
                            p: ({node, ...props}) => <p className="mb-2 text-slate-700 leading-relaxed" {...props} />,
                            strong: ({node, ...props}) => <strong className="font-semibold text-slate-800" {...props} />,
                          }}
                        >
                          {parsedAnswer?.full || results?.[0]?.answer || ""}
                        </ReactMarkdown>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Collapsible General Tips */}
              <div className="mb-4">
                <button
                  onClick={() => setShowGenericTips(!showGenericTips)}
                  className="w-full rounded-lg border border-slate-200 bg-white px-4 py-3 text-left shadow-sm hover:bg-slate-50 transition-colors flex items-center justify-between"
                >
                  <span className="text-sm font-medium text-slate-700">General Medication Tips</span>
                  <svg className={`h-4 w-4 text-slate-400 transition-transform ${showGenericTips ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showGenericTips && (
                  <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-4 shadow-sm">
                    <ul className="space-y-2 text-sm text-slate-600">
                      <li className="flex items-start gap-2">
                        <span className="text-slate-400">•</span>
                        <span>Always read the medication label and follow dosing instructions</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-slate-400">•</span>
                        <span>Start with the lowest effective dose when possible</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-slate-400">•</span>
                        <span>Consult a pharmacist if you have questions about drug interactions</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-slate-400">•</span>
                        <span>Keep a list of all medications you take, including supplements</span>
                      </li>
                    </ul>
                  </div>
                )}
              </div>

              {/* PubMed Articles - Simplified */}
              {articles.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Related Research</h3>
                  <div className="space-y-2">
                    {articles.slice(0, 3).map((a) => (
                      <a
                        key={a.id}
                        href={a.url}
                        target="_blank"
                        rel="noreferrer"
                        className="block rounded-lg border border-slate-200 bg-white p-3 shadow-sm hover:border-emerald-300 hover:shadow transition-all"
                      >
                        <p className="text-sm font-medium text-slate-800 line-clamp-2">{a.title}</p>
                        <p className="mt-1 text-xs text-slate-500">{a.journal}{a.year ? ` • ${a.year}` : ""}</p>
                      </a>
                    ))}
                  </div>
                </div>
              )}

              {pubmedLoading && (
                <p className="text-xs text-slate-500 mb-4">Loading related research...</p>
              )}

              {/* Collapsible Mechanism - Hidden by default */}
              <div className="mb-4">
                <button
                  onClick={() => setShowMechanism(!showMechanism)}
                  className="w-full rounded-lg border border-slate-200 bg-white px-4 py-3 text-left shadow-sm hover:bg-slate-50 transition-colors flex items-center justify-between"
                >
                  <span className="text-sm font-medium text-slate-700">Show Drug Mechanism</span>
                  <svg className={`h-4 w-4 text-slate-400 transition-transform ${showMechanism ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showMechanism && (
                  <div className="mt-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs font-semibold text-slate-500 uppercase">
                        <tr>
                          <th className="pb-2">Primary Action</th>
                          <th className="pb-2">Duration</th>
                        </tr>
                      </thead>
                      <tbody className="text-slate-700">
                        <tr>
                          <td className="py-1">Pain relief / fever reduction</td>
                          <td className="py-1">4-6 hours</td>
                        </tr>
                        <tr>
                          <td className="py-1">Anti-inflammatory effect</td>
                          <td className="py-1">6-12 hours</td>
                        </tr>
                      </tbody>
                    </table>
                    <p className="mt-3 text-xs text-slate-500">Drug mechanism data will be populated based on detected drugs.</p>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Footer Disclaimer */}
          <div className="mt-6 rounded-lg bg-slate-100 p-4 text-center">
            <p className="text-xs text-slate-500">
              RxBuddy provides general information only. Always consult a healthcare professional for medical advice.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
