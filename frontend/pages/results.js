import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

function extractDrugs(question) {
  const q = String(question || "").toLowerCase();
  const known = [
    { key: "acetaminophen", label: "Tylenol", aliases: ["acetaminophen", "tylenol", "paracetamol"] },
    { key: "ibuprofen", label: "Ibuprofen", aliases: ["ibuprofen", "advil", "motrin"] },
    { key: "naproxen", label: "Naproxen", aliases: ["naproxen", "aleve"] },
    { key: "aspirin", label: "Aspirin", aliases: ["aspirin"] },
    { key: "diphenhydramine", label: "Benadryl", aliases: ["diphenhydramine", "benadryl"] },
    { key: "loratadine", label: "Loratadine", aliases: ["loratadine", "claritin"] },
    { key: "cetirizine", label: "Cetirizine", aliases: ["cetirizine", "zyrtec"] },
    { key: "omeprazole", label: "Omeprazole", aliases: ["omeprazole", "prilosec"] },
    { key: "famotidine", label: "Famotidine", aliases: ["famotidine", "pepcid"] },
  ];

  const hits = [];
  for (const d of known) {
    if (d.aliases.some((a) => q.includes(a))) hits.push(d);
  }
  const uniq = [];
  const seen = new Set();
  for (const h of hits) {
    if (!seen.has(h.key)) {
      uniq.push(h);
      seen.add(h.key);
    }
  }
  return uniq.slice(0, 2);
}

function parseBullets(lines) {
  const out = [];
  for (const raw of lines) {
    const line = String(raw || "").trim();
    if (!line) continue;
    const m =
      line.match(/^[-*•]\s+(.*)$/) ||
      line.match(/^\d+\.\s+(.*)$/) ||
      line.match(/^⚠️\s*(.*)$/);
    if (m && m[1]) out.push(m[1].trim());
  }
  return out;
}

function parseClaudeAnswer(answer) {
  const text = String(answer || "").replace(/\r\n/g, "\n").trim();
  if (!text) return null;

  const first = text.split("\n").slice(0, 2).join(" ").trim();
  let yesNo = null;
  if (/^\s*yes\b/i.test(first)) yesNo = "Yes";
  else if (/^\s*no\b/i.test(first)) yesNo = "No";

  const normalized = text.replace(/\*\*(What to do|What to avoid|See a doctor if|When to see a doctor)\*\*\s*:?/gi, (_, h) => {
    return `${String(h).trim()}:`;
  });

  const lines = normalized.split("\n");
  const sections = { whatToDo: [], whatToAvoid: [], seeDoctorIf: [] };
  let current = null;
  for (const rawLine of lines) {
    const line = String(rawLine || "").trim();
    if (!line) continue;

    if (/^what to do\s*:/i.test(line)) {
      current = "whatToDo";
      continue;
    }
    if (/^what to avoid\s*:/i.test(line)) {
      current = "whatToAvoid";
      continue;
    }
    if (/^(see a doctor if|when to see a doctor)\s*:/i.test(line)) {
      current = "seeDoctorIf";
      continue;
    }
    if (current) sections[current].push(line);
  }

  const whatToDo = parseBullets(sections.whatToDo);
  const whatToAvoid = parseBullets(sections.whatToAvoid);
  const seeDoctorIf = parseBullets(sections.seeDoctorIf);
  const parsedAnything = whatToDo.length || whatToAvoid.length || seeDoctorIf.length;

  return { yesNo, lead: first, whatToDo, whatToAvoid, seeDoctorIf, parsedAnything: Boolean(parsedAnything), full: text };
}

async function fetchPubMedArticles(query) {
  const term = String(query || "").trim();
  if (!term) return [];

  const esearch = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi");
  esearch.searchParams.set("db", "pubmed");
  esearch.searchParams.set("retmode", "json");
  esearch.searchParams.set("retmax", "3");
  esearch.searchParams.set("sort", "relevance");
  esearch.searchParams.set("term", term);

  const s = await fetch(esearch.toString());
  if (!s.ok) throw new Error(`PubMed search failed (${s.status})`);
  const sData = await s.json();
  const ids = sData?.esearchresult?.idlist || [];
  if (!Array.isArray(ids) || ids.length === 0) return [];

  const esummary = new URL("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi");
  esummary.searchParams.set("db", "pubmed");
  esummary.searchParams.set("retmode", "json");
  esummary.searchParams.set("id", ids.join(","));

  const u = await fetch(esummary.toString());
  if (!u.ok) throw new Error(`PubMed summary failed (${u.status})`);
  const uData = await u.json();
  const result = uData?.result || {};

  const articles = [];
  for (const id of ids) {
    const r = result?.[id];
    if (!r) continue;
    const title = r.title ? String(r.title).replace(/\s+/g, " ").trim() : `PubMed ${id}`;
    const journal = r.fulljournalname ? String(r.fulljournalname).trim() : (r.source ? String(r.source).trim() : "PubMed");
    const pubdate = r.pubdate ? String(r.pubdate) : "";
    const yearMatch = pubdate.match(/\b(19|20)\d{2}\b/);
    const year = yearMatch ? yearMatch[0] : "";
    const url = `https://pubmed.ncbi.nlm.nih.gov/${id}/`;
    const takeaway = title.split(":").slice(0, 2).join(":").trim();
    articles.push({ id, title, journal, year, takeaway, url });
  }
  return articles;
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
  const [pubmedError, setPubmedError] = useState("");
  const [articles, setArticles] = useState([]);

  const [headerQuery, setHeaderQuery] = useState("");

  const title = useMemo(() => (q ? `Results — ${q}` : "Results"), [q]);
  const drugs = useMemo(() => extractDrugs(q), [q]);
  const topAnswer = useMemo(() => {
    const first = results?.[0];
    if (!first || typeof first.answer !== "string") return null;
    return parseClaudeAnswer(first.answer);
  }, [results]);

  useEffect(() => {
    setHeaderQuery(q || "");
  }, [q]);

  useEffect(() => {
    if (!router.isReady) return;
    if (!q) return;

    let cancelled = false;
    async function run() {
      setLoading(true);
      setError("");
      setDidYouMean(null);
      setSource("database");
      setSavedToDb(false);
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
        console.log("[RxBuddy] Full /search response:", JSON.stringify(data, null, 2));
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
    return () => {
      cancelled = true;
    };
  }, [router.isReady, q, engine]);

  useEffect(() => {
    if (!router.isReady) return;
    if (!q) return;

    let cancelled = false;
    async function runPubMed() {
      setPubmedLoading(true);
      setPubmedError("");
      try {
        const items = await fetchPubMedArticles(q);
        if (!cancelled) setArticles(items);
      } catch (e) {
        if (!cancelled) setPubmedError(e?.message || "Could not load PubMed articles.");
      } finally {
        if (!cancelled) setPubmedLoading(false);
      }
    }

    runPubMed();
    return () => {
      cancelled = true;
    };
  }, [router.isReady, q]);

  function onSubmitHeader(e) {
    e.preventDefault();
    const nextQ = String(headerQuery || "").trim();
    if (!nextQ) return;
    router.push(`/results?q=${encodeURIComponent(nextQ)}&engine=${encodeURIComponent(engine)}`);
  }

  return (
    <>
      <Head>
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"
          rel="stylesheet"
        />
      </Head>

      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-brand-50/30" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        {/* Header */}
        <div className="sticky top-0 z-20 border-b border-slate-200/70 bg-white/80 backdrop-blur">
          <div className="mx-auto max-w-6xl px-6 py-4">
            <div className="flex items-center gap-4">
              <Link href="/" className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-md">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-3-3v6m-7 4h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <div className="leading-tight">
                  <p className="text-sm font-extrabold tracking-tight text-slate-900">RxBuddy</p>
                  <p className="text-xs font-medium text-slate-500">Your pocket pharmacist</p>
                </div>
              </Link>

              <form onSubmit={onSubmitHeader} className="mx-auto hidden w-full max-w-2xl items-center gap-2 md:flex">
                <div className="flex w-full items-center rounded-full border border-slate-200 bg-white px-4 py-2.5 shadow-sm focus-within:ring-2 focus-within:ring-brand-200">
                  <svg className="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <input
                    value={headerQuery}
                    onChange={(e) => setHeaderQuery(e.target.value)}
                    placeholder="Search your medication question…"
                    className="ml-3 w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                  />
                </div>
                <button type="submit" className="rounded-full bg-brand-500 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-600">
                  Search
                </button>
              </form>

              <span className="hidden rounded-full bg-white px-4 py-1.5 text-xs font-semibold text-slate-500 ring-1 ring-slate-200 shadow-sm md:inline-flex">
                Search Engine: {engine.toUpperCase()}
              </span>
            </div>
          </div>
        </div>

        <div className="mx-auto max-w-6xl px-6 py-8">
          <div className="mb-5 md:hidden">
            <form onSubmit={onSubmitHeader} className="flex items-center gap-2">
              <div className="flex w-full items-center rounded-full border border-slate-200 bg-white px-4 py-2.5 shadow-sm focus-within:ring-2 focus-within:ring-brand-200">
                <svg className="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  value={headerQuery}
                  onChange={(e) => setHeaderQuery(e.target.value)}
                  placeholder="Search your medication question…"
                  className="ml-3 w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
              </div>
              <button type="submit" className="rounded-full bg-brand-500 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-600">
                Search
              </button>
            </form>
          </div>

          <main className="grid grid-cols-1 gap-6 lg:grid-cols-12">
            {/* Left */}
            <section className="lg:col-span-3">
              <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-lg shadow-slate-200/40">
                <h2 className="text-sm font-extrabold tracking-tight text-slate-900">Drug Visuals &amp; Mechanism</h2>

                <div className="mt-4 grid grid-cols-2 gap-3">
                  {(drugs.length ? drugs : [{ key: "drug-a", label: "Drug A" }, { key: "drug-b", label: "Drug B" }]).map((d) => (
                    <div key={d.key} className="rounded-xl border border-slate-200 bg-white p-3 text-center">
                      <div className="mx-auto h-20 w-20 overflow-hidden rounded-xl bg-slate-50 ring-1 ring-slate-200">
                        <img
                          alt={`${d.label} placeholder`}
                          className="h-full w-full object-cover"
                          src={`https://via.placeholder.com/160x160.png?text=${encodeURIComponent(d.label)}`}
                        />
                      </div>
                      <p className="mt-2 text-xs font-semibold text-slate-800">{d.label}</p>
                    </div>
                  ))}
                </div>

                <div className="mt-5">
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Mechanism</p>
                  <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-50 text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        <tr>
                          <th className="px-3 py-2">Primary Action</th>
                          <th className="px-3 py-2">Duration</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200">
                        <tr>
                          <td className="px-3 py-2 font-semibold text-slate-800">Pain relief / fever</td>
                          <td className="px-3 py-2 text-slate-700">4–6 hours</td>
                        </tr>
                        <tr>
                          <td className="px-3 py-2 font-semibold text-slate-800">Anti‑inflammatory</td>
                          <td className="px-3 py-2 text-slate-700">6–12 hours</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-slate-500">
                    Placeholders for now (we’ll auto-detect drugs + real mechanisms next).
                  </p>
                </div>
              </div>
            </section>

            {/* Center */}
            <section className="lg:col-span-6">
              <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-lg shadow-slate-200/40">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-sm font-extrabold tracking-tight text-slate-900">Direct Answer</h2>
                  <div className="flex items-center gap-2">
                    {/* Source label */}
                    {source === "database" ? (
                      <span className="rounded-full bg-emerald-50 px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-emerald-700 ring-1 ring-emerald-200">
                        💊 RxBuddy Answer
                      </span>
                    ) : (
                      <span className="rounded-full bg-violet-50 px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-violet-700 ring-1 ring-violet-200">
                        🤖 AI Answer
                      </span>
                    )}
                    {/* Saved to DB badge */}
                    {savedToDb && (
                      <span className="rounded-full bg-green-50 px-2 py-1 text-[10px] font-medium text-green-700 ring-1 ring-green-200">
                        ✅ Added to database
                      </span>
                    )}
                  </div>
                </div>

                {/* Did you mean? banner */}
                {didYouMean && (
                  <div
                    className="mt-4 cursor-pointer rounded-xl border border-amber-300 bg-amber-50 p-4 transition-colors hover:bg-amber-100"
                    onClick={() => {
                      router.push(`/results?q=${encodeURIComponent(didYouMean)}&engine=${encodeURIComponent(engine)}`);
                    }}
                  >
                    <p className="text-sm font-medium text-amber-900">
                      <span className="font-bold">Did you mean:</span>{" "}
                      <span className="underline">{didYouMean}</span>?
                    </p>
                    <p className="mt-1 text-xs text-amber-700">Click to search with corrected spelling</p>
                  </div>
                )}

                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Question</p>
                  <p className="mt-1 text-sm font-semibold text-slate-900">{q || "—"}</p>
                </div>

                {loading ? (
                  <div className="mt-6 flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-200 border-t-brand-500" />
                    <p className="text-sm font-medium text-slate-600">Generating your answer…</p>
                  </div>
                ) : error ? (
                  <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <p className="text-sm font-semibold text-rose-900">Couldn’t load results</p>
                    <p className="mt-1 text-sm text-rose-700">{error}</p>
                    <p className="mt-3 text-xs text-rose-700">
                      API base: <code className="rounded bg-rose-100 px-2 py-0.5 font-mono">{API_BASE}</code>
                    </p>
                  </div>
                ) : (
                  <>
                    <div className={`mt-6 rounded-xl border p-4 ${topAnswer?.yesNo === "No" ? "border-rose-200 bg-rose-50" : "border-emerald-200 bg-emerald-50"}`}>
                      <p className={`${topAnswer?.yesNo === "No" ? "text-rose-800" : "text-emerald-800"} text-lg font-extrabold`}>
                        {topAnswer?.yesNo ? `${topAnswer.yesNo},` : "Direct answer"}
                      </p>
                      <p className="mt-1 text-sm font-medium text-slate-700">
                        {topAnswer?.lead || (results?.[0]?.answer ? "Answer generated from Claude." : "Generating answer…")}
                      </p>
                    </div>

                    <div className="mt-5 space-y-4">
                      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                        <p className="text-sm font-extrabold text-emerald-900">What to Do</p>
                        <ul className="mt-3 space-y-2 text-sm text-emerald-950">
                          {(topAnswer?.whatToDo?.length ? topAnswer.whatToDo : ["Follow the package directions.", "Use the lowest effective dose.", "If you’re unsure, ask your pharmacist."]).map(
                            (b, i) => (
                              <li key={i} className="flex gap-2">
                                <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
                                <span className="leading-relaxed">{b}</span>
                              </li>
                            )
                          )}
                        </ul>
                      </div>

                      <div className="rounded-xl border border-rose-200 bg-rose-50 p-4">
                        <p className="text-sm font-extrabold text-rose-900">What to Avoid</p>
                        <ul className="mt-3 space-y-2 text-sm text-rose-950">
                          {(topAnswer?.whatToAvoid?.length ? topAnswer.whatToAvoid : ["Avoid taking more than the max daily dose.", "Avoid combining products with the same ingredient.", "Avoid alcohol if it worsens side effects."]).map(
                            (b, i) => (
                              <li key={i} className="flex gap-2">
                                <span className="shrink-0 text-rose-700">⚠️</span>
                                <span className="leading-relaxed">{b}</span>
                              </li>
                            )
                          )}
                        </ul>
                      </div>

                      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                        <p className="text-sm font-extrabold text-amber-900">See a Doctor If</p>
                        <ul className="mt-3 space-y-2 text-sm text-amber-950">
                          {(topAnswer?.seeDoctorIf?.length ? topAnswer.seeDoctorIf : ["Symptoms are severe or getting worse.", "You have chest pain, trouble breathing, or fainting.", "You’re pregnant or have kidney/liver disease."]).map(
                            (b, i) => (
                              <li key={i} className="flex gap-2">
                                <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-amber-500" />
                                <span className="leading-relaxed">{b}</span>
                              </li>
                            )
                          )}
                        </ul>
                      </div>
                    </div>

                    {results?.[0]?.answer && topAnswer && !topAnswer.parsedAnything && (
                      <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Full answer</p>
                        <div className="mt-3 prose prose-sm prose-slate max-w-none">
                          <ReactMarkdown>{String(results[0].answer)}</ReactMarkdown>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>

            {/* Right */}
            <section className="lg:col-span-3">
              <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-lg shadow-slate-200/40">
                <h2 className="text-sm font-extrabold tracking-tight text-slate-900">Authoritative Scholar Articles</h2>
                <p className="mt-1 text-xs text-slate-500">Real results from PubMed (NCBI).</p>

                {pubmedLoading ? (
                  <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-sm font-medium text-slate-600">Fetching PubMed articles…</p>
                  </div>
                ) : pubmedError ? (
                  <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4">
                    <p className="text-sm font-semibold text-rose-900">Couldn’t load PubMed</p>
                    <p className="mt-1 text-sm text-rose-700">{pubmedError}</p>
                  </div>
                ) : articles.length === 0 ? (
                  <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <p className="text-sm font-medium text-slate-600">No articles found yet.</p>
                  </div>
                ) : (
                  <div className="mt-4 space-y-3">
                    {articles.slice(0, 3).map((a) => (
                      <div key={a.id} className="rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-sm font-bold leading-snug text-slate-900">{a.title}</p>
                        <p className="mt-1 text-xs font-medium text-slate-500">
                          {a.journal}
                          {a.year ? `, ${a.year}` : ""}
                        </p>
                        <div className="mt-3 rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200">
                          <p className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Key takeaway</p>
                          <p className="mt-1 text-xs text-slate-700">{a.takeaway}</p>
                        </div>
                        <a
                          href={a.url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-3 inline-flex w-full items-center justify-center rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs font-semibold text-brand-700 hover:bg-brand-100"
                        >
                          PubMed
                        </a>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </main>

          <div className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-5 text-xs text-slate-500 shadow-sm">
            RxBuddy provides general information and is not a substitute for professional medical advice. If symptoms are severe or you feel unsafe, seek urgent care.
          </div>
        </div>
      </div>
    </>
  );
}
