import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";

const CATEGORIES = [
  "Drug Interactions",
  "Cold & Flu",
  "Pain Relief",
  "Allergies",
  "Sleep",
  "Dosage",
  "Pregnancy",
  "Children",
];

function getSpeechRecognition() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export default function HomePage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [supportsVoice, setSupportsVoice] = useState(false);
  const recognitionRef = useRef(null);

  useEffect(() => {
    setSupportsVoice(!!getSpeechRecognition());
  }, []);

  useEffect(() => {
    if (!supportsVoice) return;
    const SpeechRecognition = getSpeechRecognition();
    if (!SpeechRecognition) return;

    const rec = new SpeechRecognition();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      const last = event.results[event.results.length - 1];
      const transcript = last?.[0]?.transcript ?? "";
      if (transcript) setQuery(transcript);
      if (last?.isFinal) setListening(false);
    };

    rec.onerror = (e) => {
      setListening(false);
      setVoiceError(e?.error ? `Voice error: ${e.error}` : "Voice error");
    };

    rec.onend = () => setListening(false);
    recognitionRef.current = rec;

    return () => {
      try {
        rec.abort();
      } catch {}
      recognitionRef.current = null;
    };
  }, [supportsVoice]);

  function goSearch(q) {
    const trimmed = (q || "").trim();
    if (!trimmed) return;
    router.push({
      pathname: "/results",
      query: { q: trimmed, engine: "tfidf" },
    });
  }

  function toggleMic() {
    setVoiceError("");
    if (!supportsVoice) {
      setVoiceError("Voice input is not supported in this browser.");
      return;
    }
    const rec = recognitionRef.current;
    if (!rec) return;

    if (listening) {
      try {
        rec.stop();
      } catch {}
      setListening(false);
      return;
    }

    try {
      rec.start();
      setListening(true);
    } catch (e) {
      setListening(false);
      setVoiceError("Could not start the microphone. Try again.");
    }
  }

  return (
    <>
      <Head>
        <title>RxBuddy — Your pocket pharmacist</title>
        <meta
          name="description"
          content="Ask everyday medication questions and get fast, plain-English help."
        />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </Head>

      <div className="min-h-screen bg-gradient-to-br from-brand-50 via-white to-brand-50/30" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        <div className="mx-auto max-w-2xl px-6 py-16">
          {/* Header / Logo */}
          <header className="text-center">
            <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-lg shadow-brand-500/30">
              <svg className="h-10 w-10" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-3-3v6m-7 4h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <h1 className="text-4xl font-bold tracking-tight text-slate-900">
              RxBuddy
            </h1>
            <p className="mt-3 text-lg text-slate-500">
              Your pocket pharmacist
            </p>
          </header>

          {/* Search Card */}
          <main className="mt-12">
            <div className="rounded-3xl border border-slate-200/80 bg-white p-8 shadow-xl shadow-slate-200/50">
              <label className="mb-4 block text-base font-semibold text-slate-800">
                Ask a medication question
              </label>

              <div className="flex items-center gap-3">
                <div className="relative flex-1">
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") goSearch(query);
                    }}
                    className="w-full rounded-2xl border-2 border-slate-200 bg-slate-50 px-5 py-4 text-base text-slate-900 placeholder:text-slate-400 transition-all focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-brand-500/10"
                    placeholder="e.g. can I take ibuprofen with blood pressure meds?"
                  />
                </div>

                <button
                  type="button"
                  onClick={toggleMic}
                  className={`flex h-14 w-14 items-center justify-center rounded-2xl text-xl transition-all ${
                    listening
                      ? "bg-brand-500 text-white shadow-lg shadow-brand-500/30 scale-105"
                      : "border-2 border-slate-200 bg-white text-slate-500 hover:border-brand-300 hover:text-brand-600 hover:bg-brand-50"
                  }`}
                  aria-label="Voice input"
                  title="Voice input"
                >
                  {listening ? (
                    <span className="animate-pulse">●</span>
                  ) : (
                    <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => goSearch(query)}
                  className="flex h-14 items-center gap-2 rounded-2xl bg-gradient-to-r from-brand-500 to-brand-600 px-6 text-base font-semibold text-white shadow-lg shadow-brand-500/30 transition-all hover:shadow-xl hover:shadow-brand-500/40 hover:scale-[1.02] active:scale-[0.98]"
                >
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  Search
                </button>
              </div>

              {voiceError ? (
                <p className="mt-4 text-sm font-medium text-rose-600">{voiceError}</p>
              ) : supportsVoice ? (
                <p className="mt-4 text-sm text-slate-400">
                  💡 Tip: Tap the microphone and speak your question
                </p>
              ) : (
                <p className="mt-4 text-sm text-slate-400">
                  Voice input works best in Chrome or Edge
                </p>
              )}
            </div>

            {/* Category Buttons */}
            <section className="mt-10">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
                Browse by category
              </h2>
              <div className="flex flex-wrap gap-3">
                {CATEGORIES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => goSearch(c)}
                    className="rounded-full border-2 border-slate-200 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 transition-all hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700 hover:shadow-md active:scale-95"
                  >
                    {c}
                  </button>
                ))}
              </div>
            </section>

            {/* Disclaimer */}
            <section className="mt-12 rounded-2xl bg-gradient-to-r from-brand-50 to-brand-100/50 p-6 border border-brand-200/50">
              <div className="flex items-start gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-500/10 text-brand-600">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-brand-900">
                    Friendly reminder
                  </h3>
                  <p className="mt-1 text-sm leading-relaxed text-brand-800/80">
                    RxBuddy provides general information only. For emergencies, call your local emergency number.
                    For personal medical advice, always consult a licensed pharmacist or healthcare provider.
                  </p>
                </div>
              </div>
            </section>
          </main>
        </div>
      </div>
    </>
  );
}
