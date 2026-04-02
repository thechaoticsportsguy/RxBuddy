import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import Disclaimer from "../components/Disclaimer";
import AnswerCard from "../components/AnswerCard";
import dynamic from "next/dynamic";

// Lazy-load the 3D pill component (Three.js is client-only, no SSR)
const NonDrugQuery = dynamic(() => import("../components/NonDrugQuery"), {
  ssr: false,
  loading: () => (
    <div style={{ width: "100%", height: 420, background: "#060c1a", borderRadius: 16 }} />
  ),
});

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
        // image fetch failure is non-fatal
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

  // No body-text verdict guessing — the backend structured.verdict is the authority

  const result = {
    verdict,
    why,
    importantNotes,
    medicalHelp,
    full: text,
    hasContent: Boolean(verdict || why || importantNotes.length || medicalHelp.length),
  };

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

    // BUG 3 FIX: If fewer than 2 relevant articles, add fallback safety articles
    if (relevantArticles.length < 2) {
      const combined = [...relevantArticles, ...FALLBACK_SAFETY_ARTICLES];
      return combined.slice(0, 3);
    }

    return relevantArticles.slice(0, 3);
  } catch {
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
  const [streamStatus, setStreamStatus] = useState("");

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

  // For side_effects intent, always hide the "Detailed Explanation" collapsible
  // because the AnswerCard already renders the structured side-effects sections.
  const shouldBypassDetailedExplanation = useMemo(() => {
    const structured = results?.[0]?.structured || {};
    return structured.intent === "side_effects";
  }, [results]);

  useEffect(() => { setHeaderQuery(q || ""); }, [q]);

  useEffect(() => {
    if (!router.isReady || !q) return;
    let cancelled = false;

    async function run() {
      setLoading(true);
      setError("");
      setStreamStatus("");
      setDidYouMean(null);
      setSource("database");
      setSavedToDb(false);
      setShowFullAnswer(false);

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 60000);

      // ── Try streaming endpoint first ────────────────────────────────────
      try {
        const res = await fetch(`${API_BASE}/v2/search/stream`, {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, engine, top_k: 5 }),
          signal: controller.signal,
        });

        if (res.ok && res.body) {
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let gotResult = false;

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();
            for (const line of lines) {
              if (!line.startsWith("data: ")) continue;
              let evt;
              try { evt = JSON.parse(line.slice(6)); } catch { continue; }

              if (evt.type === "status" && !cancelled) {
                setStreamStatus(evt.message || "");
              } else if (evt.type === "done" && !cancelled) {
                const r = evt.result;
                // NON_DRUG pipeline responses are flat dicts — wrap them so
                // AnswerCard receives result.structured correctly.
                if (r && (r.verdict === "NON_DRUG" || r.intent === "non_drug_query")) {
                  setResults([{ id: 0, question: q, answer: "", structured: r }]);
                } else {
                  setResults(r ? [r] : []);
                }
                setSource(evt.source || "ai_generated");
                setSavedToDb(false);
                gotResult = true;
              } else if (evt.type === "error") {
                throw new Error(evt.message || "Stream error");
              }
            }
          }

          clearTimeout(timeoutId);
          if (!cancelled) setStreamStatus("");
          if (gotResult) {
            if (!cancelled) setLoading(false);
            return;
          }
        }
      } catch (streamErr) {
        if (streamErr?.name === "AbortError") {
          clearTimeout(timeoutId);
          if (!cancelled) {
            setStreamStatus("");
            setError("This is taking longer than usual. Please try your search again.");
            setLoading(false);
          }
          return;
        }
        // Stream failed — fall through to plain /search
      }

      // ── Fallback: plain /search ─────────────────────────────────────────
      if (cancelled) return;
      setStreamStatus("Searching...");
      try {
        const res = await fetch(`${API_BASE}/v2/search`, {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: q, engine, top_k: 5 }),
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
          const text = await res.text().catch(() => "");
          throw new Error(text || `Request failed (${res.status})`);
        }

        let data;
        try { data = await res.json(); }
        catch { throw new Error("Server returned an unreadable response. Please try again."); }

        if (!cancelled) {
          // NON_DRUG responses are flat dicts with no `results` array
          if (data.verdict === "NON_DRUG" || data.intent === "non_drug_query") {
            setResults([{ id: 0, question: q, answer: "", structured: data }]);
            setSource("pipeline_v2");
          } else {
            setResults(Array.isArray(data.results) ? data.results : []);
            setDidYouMean(data.did_you_mean || null);
            setSource(data.source || "database");
            setSavedToDb(data.saved_to_db || false);
          }
        }
      } catch (e) {
        clearTimeout(timeoutId);
        if (!cancelled) {
          if (e?.name === "AbortError") {
            setError("This is taking longer than usual. Please try your search again.");
          } else {
            console.error("Fetch failed:", e);
            setResults([{
              id: 0,
              question: q,
              answer: "We couldn't load results right now. Please try again in a moment.",
              structured: {
                verdict: "CAUTION",
                intent: "error",
                answer: "We couldn't load results right now. Please try again in a moment.",
                common_side_effects: [],
                serious_side_effects: [],
                warning_signs: [],
                what_to_do: ["Try again in a moment"],
              },
            }]);
          }
        }
      } finally {
        if (!cancelled) {
          setStreamStatus("");
          setLoading(false);
        }
      }
    }

    run();
    return () => { cancelled = true; };
  }, [router.isReady, q, engine]);

  // Timer-based loading status messages (shown when loading & no SSE status yet)
  const [loadingMessage, setLoadingMessage] = useState("");
  useEffect(() => {
    if (!loading) { setLoadingMessage(""); return; }
    const t1 = setTimeout(() => setLoadingMessage("Checking additional sources..."), 8000);
    const t2 = setTimeout(() => setLoadingMessage("Almost there..."), 20000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [loading]);

  useEffect(() => {
    if (!router.isReady || !q) return;
    let cancelled = false;

    async function runPubMed() {
      setPubmedLoading(true);
      try {
        const items = await fetchPubMedArticles(q);
        if (!cancelled) setArticles(items);
      } catch {
        // PubMed fetch failure is non-fatal
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

  // Verdict rendering is handled by AnswerCard component


  return (
    <>
      <Head>
        <title>{title} — RxBuddy</title>
        <meta name="description" content="Medication information from DailyMed and Drugs@FDA. Not medical advice — always consult a licensed healthcare provider." />
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
                    ) : source === "dataset" ? (
                      <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">Dataset</span>
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
            <div className="rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              {/* Banner skeleton */}
              <div className="flex items-center gap-3 px-5 py-4 bg-slate-100 border-b border-slate-200">
                <div className="h-9 w-9 rounded-full bg-slate-300 animate-pulse shrink-0" />
                <div className="h-5 w-28 rounded-full bg-slate-300 animate-pulse" />
              </div>
              {/* Body skeleton */}
              <div className="p-5 space-y-4 bg-white">
                <div className="h-4 w-3/4 rounded bg-slate-200 animate-pulse" />
                <div className="h-4 w-1/2 rounded bg-slate-200 animate-pulse" />
                <div className="space-y-2 pt-1">
                  <div className="h-3 w-1/3 rounded bg-slate-200 animate-pulse" />
                  <div className="h-3 w-2/3 rounded bg-slate-200 animate-pulse" />
                  <div className="h-3 w-1/2 rounded bg-slate-200 animate-pulse" />
                </div>
                <div className="space-y-2 pt-1">
                  <div className="h-3 w-1/3 rounded bg-slate-200 animate-pulse" />
                  <div className="h-3 w-3/5 rounded bg-slate-200 animate-pulse" />
                  <div className="h-3 w-2/5 rounded bg-slate-200 animate-pulse" />
                </div>
                <div className="pt-2 text-center text-sm text-slate-500 min-h-[1.5rem]">
                  {streamStatus || loadingMessage}
                </div>
              </div>
            </div>
          ) : error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 shadow-sm">
              <p className="font-semibold text-rose-800">Error loading results</p>
              <p className="mt-1 text-sm text-rose-700">{error}</p>
            </div>
          ) : (
            <>
              {/* Non-drug query → 3D pill rejection page */}
              {(() => {
                const s = results?.[0]?.structured || {};
                if (s.intent === "non_drug_query" || s.verdict === "NON_DRUG") {
                  const illegal = (s.answer || "").includes("SAMHSA");
                  return (
                    <div className="mb-4">
                      <NonDrugQuery
                        query={q}
                        message={s.answer || s.short_answer}
                        isIllegal={illegal}
                      />
                    </div>
                  );
                }
                return null;
              })()}

              {/* Single-answer card — Phase 4 (skip if non-drug was rendered) */}
              {!(results?.[0]?.structured?.intent === "non_drug_query" ||
                 results?.[0]?.structured?.verdict === "NON_DRUG") && (
              <div className="mb-4">
                <AnswerCard
                  result={results?.[0]}
                  query={q}
                />
              </div>
              )}

              {/* Collapsible Full Answer - BUG 2 FIX: Better markdown rendering with prose styling */}
              {!shouldBypassDetailedExplanation && (parsedAnswer?.full || results?.[0]?.answer) && (
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
              