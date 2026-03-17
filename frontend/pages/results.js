import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

function pct(score) {
  if (score === null || score === undefined) return null;
  const n = Number(score);
  if (Number.isNaN(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

export default function ResultsPage() {
  const router = useRouter();
  const q = typeof router.query.q === "string" ? router.query.q : "";
  const engine = typeof router.query.engine === "string" ? router.query.engine : "tfidf";

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState([]);

  const title = useMemo(() => (q ? `Results — ${q}` : "Results"), [q]);

  useEffect(() => {
    if (!router.isReady) return;
    if (!q) return;

    let cancelled = false;
    async function run() {
      setLoading(true);
      setError("");
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
        if (!cancelled) setResults(Array.isArray(data.results) ? data.results : []);
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

  return (
    <>
      <Head>
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </Head>

      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-brand-50/30" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        <div className="mx-auto max-w-2xl px-6 py-10">
          {/* Nav */}
          <div className="flex items-center justify-between">
            <Link
              href="/"
              className="inline-flex items-center gap-3 text-sm font-semibold text-slate-600 transition-colors hover:text-brand-600"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-md">
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-3-3v6m-7 4h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <span>Back to search</span>
            </Link>

            <span className="rounded-full bg-white px-4 py-1.5 text-xs font-semibold text-slate-500 ring-1 ring-slate-200 shadow-sm">
              {engine.toUpperCase()} engine
            </span>
          </div>

          {/* Query Header */}
          <header className="mt-8 rounded-2xl border border-slate-200/80 bg-white p-6 shadow-lg shadow-slate-200/50">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Your question</p>
            <h1 className="mt-2 text-2xl font-bold text-slate-900">{q || "—"}</h1>
          </header>

          {/* Results */}
          <main className="mt-8">
            {loading ? (
              <div className="flex items-center justify-center rounded-2xl border border-slate-200/80 bg-white p-12 shadow-lg">
                <div className="flex flex-col items-center gap-4">
                  <div className="h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-500"></div>
                  <p className="text-sm font-medium text-slate-500">Searching...</p>
                </div>
              </div>
            ) : error ? (
              <div className="rounded-2xl border border-rose-200 bg-gradient-to-br from-rose-50 to-white p-8 shadow-lg">
                <div className="flex items-start gap-4">
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-rose-600">
                    <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.732-.833-2.5 0L4.27 16.5c-.77.833.192 2.5 1.732 2.5z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-lg font-semibold text-rose-900">Couldn't load results</p>
                    <p className="mt-1 text-sm text-rose-700">{error}</p>
                    <p className="mt-4 text-sm text-rose-600">
                      Make sure FastAPI is running on{" "}
                      <code className="rounded bg-rose-100 px-2 py-0.5 font-mono text-xs">{API_BASE}</code>
                    </p>
                  </div>
                </div>
              </div>
            ) : results.length === 0 ? (
              <div className="rounded-2xl border border-slate-200/80 bg-white p-8 text-center shadow-lg">
                <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-slate-100 text-slate-400">
                  <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </div>
                <p className="mt-4 text-lg font-semibold text-slate-700">No matches found</p>
                <p className="mt-2 text-sm text-slate-500">
                  Try different wording, like "safe cold medicine with diabetes"
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {results.map((r, idx) => {
                  const scorePct = pct(r.score);
                  return (
                    <div
                      key={r.id}
                      className="group rounded-2xl border border-slate-200/80 bg-white p-6 shadow-lg shadow-slate-200/50 transition-all hover:shadow-xl hover:border-brand-200"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-100 text-xs font-bold text-brand-700">
                              {idx + 1}
                            </span>
                            <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700">
                              {r.category || "General"}
                            </span>
                          </div>
                          <p className="mt-3 text-lg font-semibold leading-snug text-slate-900 group-hover:text-brand-700 transition-colors">
                            {r.question}
                          </p>

                          {typeof r.answer === "string" && r.answer.trim().length > 0 && (
                            <div className="mt-4 rounded-xl border border-brand-200/60 bg-brand-50/70 p-4">
                              <p className="text-xs font-bold uppercase tracking-wider text-brand-700">
                                💊 RxBuddy Answer
                              </p>
                              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-700">
                                {r.answer}
                              </p>
                            </div>
                          )}
                        </div>
                        {scorePct !== null && (
                          <div className="shrink-0 rounded-2xl bg-gradient-to-br from-brand-50 to-brand-100 px-4 py-3 text-center shadow-inner">
                            <p className="text-[10px] font-bold uppercase tracking-wider text-brand-600">Match</p>
                            <p className="text-2xl font-bold text-brand-700">
                              {scorePct}
                              <span className="text-sm">%</span>
                            </p>
                          </div>
                        )}
                      </div>

                      {scorePct !== null && (
                        <div className="mt-4">
                          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-brand-400 to-brand-500 transition-all duration-500"
                              style={{ width: `${scorePct}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Search again */}
            <div className="mt-10 text-center">
              <Link
                href="/"
                className="inline-flex items-center gap-2 rounded-full bg-brand-500 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-brand-500/30 transition-all hover:bg-brand-600 hover:shadow-xl"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                Search again
              </Link>
            </div>
          </main>
        </div>
      </div>
    </>
  );
}
