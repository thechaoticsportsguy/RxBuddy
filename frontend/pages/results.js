import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

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
 * Build PubMed search query from user question
 */
function buildPubMedQuery(query) {
  const q = String(query || "").toLowerCase().trim();
  if (!q) return "";

  const brandToGeneric = {
    tylenol: "acetaminophen", advil: "ibuprofen", motrin: "ibuprofen",
    aleve: "naproxen", bayer: "aspirin", excedrin: "acetaminophen",
    benadryl: "diphenhydramine", claritin: "loratadine", zyrtec: "cetirizine",
    allegra: "fexofenadine", prilosec: "omeprazole", nexium: "esomeprazole",
    pepcid: "famotidine", xanax: "alprazolam", valium: "diazepam",
    ambien: "zolpidem", zoloft: "sertraline", prozac: "fluoxetine",
    lexapro: "escitalopram", lipitor: "atorvastatin", crestor: "rosuvastatin",
  };

  const genericDrugs = [
    "ibuprofen", "acetaminophen", "aspirin", "naproxen", "amoxicillin",
    "metformin", "lisinopril", "omeprazole", "gabapentin", "sertraline",
    "fluoxetine", "escitalopram", "prednisone", "azithromycin", "metoprolol",
    "losartan", "amlodipine", "atorvastatin", "levothyroxine", "alprazolam",
  ];

  const topicMappings = {
    "empty stomach": "food administration",
    "with food": "food administration",
    food: "food drug",
    pregnancy: "pregnancy",
    pregnant: "pregnancy",
    alcohol: "alcohol interaction",
    "side effect": "adverse effects",
    interaction: "drug interaction",
    children: "pediatric",
    liver: "hepatic",
    kidney: "renal",
  };

  let drugName = "";
  for (const [brand, generic] of Object.entries(brandToGeneric)) {
    if (q.includes(brand)) { drugName = generic; break; }
  }
  if (!drugName) {
    for (const drug of genericDrugs) {
      if (q.includes(drug)) { drugName = drug; break; }
    }
  }

  let topic = "";
  for (const [keyword, pubmedTerm] of Object.entries(topicMappings)) {
    if (q.includes(keyword)) { topic = pubmedTerm; break; }
  }

  if (drugName && topic) return `${drugName} ${topic}`;
  if (drugName) return `${drugName} pharmacology`;
  if (topic) return `medication ${topic}`;

  const stopWords = new Set(["can", "i", "is", "it", "the", "a", "an", "to", "with", "my", "me", "if", "or", "and", "of", "for", "on", "in", "at", "this", "that", "what", "how", "does", "do", "should", "would", "could", "will", "take", "taking", "use", "using", "safe", "okay", "ok", "be", "am"]);
  const words = q.split(/\s+/).filter(w => w.length > 3 && !stopWords.has(w));
  return words.slice(0, 3).join(" ") || "medication safety";
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchPubMedArticles(query) {
  const rawQuery = String(query || "").trim();
  if (!rawQuery) return [];

  const searchTerm = buildPubMedQuery(rawQuery);
  console.log("[PubMed] Search term:", searchTerm);
  if (!searchTerm) return [];

  try {
    const esearchUrl = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi");
    esearchUrl.searchParams.set("db", "pubmed");
    esearchUrl.searchParams.set("term", searchTerm);
    esearchUrl.searchParams.set("retmax", "3");
    esearchUrl.searchParams.set("retmode", "json");
    esearchUrl.searchParams.set("sort", "relevance");

    const searchRes = await fetch(esearchUrl.toString());
    if (!searchRes.ok) return [];

    const searchData = await searchRes.json();
    const ids = searchData?.esearchresult?.idlist || [];
    if (!Array.isArray(ids) || ids.length === 0) return [];

    await sleep(200);

    const esummaryUrl = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi");
    esummaryUrl.searchParams.set("db", "pubmed");
    esummaryUrl.searchParams.set("id", ids.join(","));
    esummaryUrl.searchParams.set("retmode", "json");

    const summaryRes = await fetch(esummaryUrl.toString());
    if (!summaryRes.ok) return [];

    const summaryData = await summaryRes.json();
    const results = summaryData?.result || {};

    const articles = [];
    for (const id of ids) {
      const article = results[id];
      if (!article) continue;

      const title = article.title ? String(article.title).replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim() : `PubMed Article ${id}`;
      const journal = article.fulljournalname ? String(article.fulljournalname).trim() : (article.source ? String(article.source).trim() : "PubMed");
      const pubdate = article.pubdate ? String(article.pubdate) : "";
      const yearMatch = pubdate.match(/\b(19|20)\d{2}\b/);
      const year = yearMatch ? yearMatch[0] : "";

      let takeaway = title.length > 100 ? title.substring(0, 97) + "..." : title;

      articles.push({ id: String(id), title, journal, year, url: `https://pubmed.ncbi.nlm.nih.gov/${id}/`, takeaway });
    }

    return articles;
  } catch (error) {
    console.error("[PubMed] Error:", error);
    return [];
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
  const verdictStyles = {
    YES:         { bg: "bg-emerald-50", border: "border-emerald-300", text: "text-emerald-800", icon: "✅", label: "YES" },
    USUALLY_YES: { bg: "bg-emerald-50", border: "border-emerald-300", text: "text-emerald-800", icon: "✅", label: "USUALLY YES" },
    NO:          { bg: "bg-rose-50",    border: "border-rose-300",    text: "text-rose-800",    icon: "❌", label: "NO" },
    NEEDS_REVIEW:{ bg: "bg-amber-50",   border: "border-amber-300",   text: "text-amber-800",   icon: "⚠️", label: "NEEDS REVIEW" },
  };

  const currentVerdict = parsedAnswer?.verdict ? verdictStyles[parsedAnswer.verdict] : null;

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

          {/* Question Card */}
          <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Your Question</p>
                <p className="mt-1 text-lg font-semibold text-slate-900">{q || "-"}</p>
              </div>
              <div className="flex items-center gap-2">
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
              {/* ANSWER BLOCK — big YES / NO / USUALLY YES / NEEDS REVIEW */}
              {currentVerdict && (
                <div className={`mb-4 rounded-xl border-2 ${currentVerdict.border} ${currentVerdict.bg} p-5 shadow-sm`}>
                  <div className="flex items-start gap-3">
                    <span className="text-3xl leading-none">{currentVerdict.icon}</span>
                    <div className="flex-1 min-w-0">
                      <span className={`text-xl font-bold ${currentVerdict.text}`}>{currentVerdict.label}</span>
                      {parsedAnswer?.why && (
                        <p className="mt-2 text-base text-slate-700 leading-relaxed">{parsedAnswer.why}</p>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Fallback if no verdict parsed but we have a "why" */}
              {!currentVerdict && parsedAnswer?.why && (
                <div className="mb-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  <p className="text-base text-slate-800">{parsedAnswer.why}</p>
                </div>
              )}

              {/* Important Notes — green box */}
              {parsedAnswer?.importantNotes?.length > 0 && (
                <div className="mb-4 rounded-lg border border-emerald-200 bg-white p-4 shadow-sm">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">📋</span>
                    <h3 className="font-semibold text-emerald-800">Important Notes</h3>
                  </div>
                  <ul className="space-y-1.5">
                    {parsedAnswer.importantNotes.map((item, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                        <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-emerald-400 shrink-0" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Get Medical Help Now If — red/amber box */}
              {parsedAnswer?.medicalHelp?.length > 0 && (
                <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-4 shadow-sm">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">🚨</span>
                    <h3 className="font-semibold text-rose-800">Get Medical Help Now If</h3>
                  </div>
                  <ul className="space-y-1.5">
                    {parsedAnswer.medicalHelp.map((item, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                        <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-rose-400 shrink-0" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Collapsible Full Answer */}
              {parsedAnswer?.full && (
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
                      <div className="prose prose-sm prose-slate max-w-none">
                        <ReactMarkdown>{parsedAnswer.full}</ReactMarkdown>
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
