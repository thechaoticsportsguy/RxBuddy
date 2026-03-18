import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useRef, useState, useCallback } from "react";

const CATEGORIES = [
  "Drug Interactions",
  "Pain Relief",
  "Allergies",
  "Cold & Flu",
  "Sleep",
  "Dosage",
  "Pregnancy",
  "Side Effects",
];

const STATS = [
  { value: "10,000+", label: "Questions" },
  { value: "FDA", label: "Verified" },
  { value: "Free", label: "Forever" },
];

const PLACEHOLDER_TEXT = "Ask about any medication...";

// Decorative background pills with fixed positions to avoid hydration mismatch
const FLOATING_PILLS = [
  { w: 40, h: 16, x: 8, y: 12, dur: 10, delay: 0, rot: 15 },
  { w: 55, h: 20, x: 85, y: 8, dur: 12, delay: 1, rot: -10 },
  { w: 30, h: 12, x: 15, y: 75, dur: 9, delay: 2, rot: 30 },
  { w: 50, h: 18, x: 78, y: 70, dur: 11, delay: 0.5, rot: -20 },
  { w: 35, h: 14, x: 5, y: 45, dur: 13, delay: 3, rot: 5 },
  { w: 45, h: 16, x: 92, y: 40, dur: 10, delay: 1.5, rot: -35 },
  { w: 25, h: 10, x: 50, y: 5, dur: 14, delay: 2.5, rot: 45 },
  { w: 60, h: 22, x: 30, y: 90, dur: 8, delay: 0, rot: -5 },
  { w: 20, h: 8, x: 65, y: 85, dur: 15, delay: 3.5, rot: 25 },
  { w: 38, h: 14, x: 42, y: 55, dur: 11, delay: 1, rot: -15 },
  { w: 28, h: 10, x: 20, y: 30, dur: 12, delay: 4, rot: 10 },
  { w: 48, h: 18, x: 70, y: 25, dur: 9, delay: 2, rot: -30 },
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

  // Animation phases: entry → breathing → splitting → morphing → ready
  const [phase, setPhase] = useState("entry");
  const [typedText, setTypedText] = useState("");
  const inputRef = useRef(null);

  // Phase sequencing
  useEffect(() => {
    const t1 = setTimeout(() => setPhase("breathing"), 800);
    const t2 = setTimeout(() => setPhase("splitting"), 1800);
    const t3 = setTimeout(() => setPhase("morphing"), 2500);
    const t4 = setTimeout(() => setPhase("ready"), 3500);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); clearTimeout(t4); };
  }, []);

  // Typing animation for placeholder
  useEffect(() => {
    if (phase !== "ready") return;
    let i = 0;
    const interval = setInterval(() => {
      if (i <= PLACEHOLDER_TEXT.length) {
        setTypedText(PLACEHOLDER_TEXT.slice(0, i));
        i++;
      } else {
        clearInterval(interval);
      }
    }, 50);
    return () => clearInterval(interval);
  }, [phase]);

  // Voice recognition setup
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
      try { rec.abort(); } catch {}
      recognitionRef.current = null;
    };
  }, [supportsVoice]);

  const goSearch = useCallback((q) => {
    const trimmed = (q || "").trim();
    if (!trimmed) return;
    router.push({ pathname: "/results", query: { q: trimmed, engine: "tfidf" } });
  }, [router]);

  function toggleMic() {
    setVoiceError("");
    if (!supportsVoice) {
      setVoiceError("Voice input is not supported in this browser.");
      return;
    }
    const rec = recognitionRef.current;
    if (!rec) return;
    if (listening) {
      try { rec.stop(); } catch {}
      setListening(false);
      return;
    }
    try {
      rec.start();
      setListening(true);
    } catch {
      setListening(false);
      setVoiceError("Could not start the microphone. Try again.");
    }
  }

  const phaseIndex = ["entry", "breathing", "splitting", "morphing", "ready"].indexOf(phase);
  const isReady = phase === "ready";

  return (
    <>
      <Head>
        <title>RxBuddy — Your pocket pharmacist</title>
        <meta name="description" content="Ask everyday medication questions and get fast, plain-English help." />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </Head>

      <style jsx global>{`
        @keyframes pillEntrance {
          0%   { opacity: 0; transform: scale(0.5); }
          70%  { transform: scale(1.05); }
          100% { opacity: 1; transform: scale(1); }
        }

        @keyframes pillBreath {
          0%, 100% { transform: scale(1); box-shadow: 0 20px 60px rgba(82,183,136,0.3); }
          50%      { transform: scale(1.03); box-shadow: 0 20px 80px rgba(82,183,136,0.5); }
        }

        @keyframes splitTop {
          0%   { transform: translateY(0) rotateX(0deg); opacity: 1; }
          100% { transform: translateY(-100px) rotateX(-60deg); opacity: 0; }
        }

        @keyframes splitBottom {
          0%   { transform: translateY(0) rotateX(0deg); opacity: 1; }
          100% { transform: translateY(100px) rotateX(60deg); opacity: 0; }
        }

        @keyframes searchExpand {
          0%   { width: 280px; opacity: 0; }
          100% { width: min(600px, 90vw); opacity: 1; }
        }

        @keyframes fadeUp {
          0%   { opacity: 0; transform: translateY(20px); }
          100% { opacity: 1; transform: translateY(0); }
        }

        @keyframes blink {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0; }
        }

        @keyframes floatPill {
          0%, 100% { transform: translateY(0px) rotate(var(--pill-rot)); }
          50%      { transform: translateY(-20px) rotate(calc(var(--pill-rot) + 5deg)); }
        }

        @keyframes slideUp {
          0%   { opacity: 0; transform: translateY(30px); }
          100% { opacity: 1; transform: translateY(0); }
        }

        @keyframes titleFadeIn {
          0%   { opacity: 0; transform: translateY(-10px); }
          100% { opacity: 1; transform: translateY(0); }
        }

        .pill-entrance {
          animation: pillEntrance 0.8s ease-out forwards;
        }

        .pill-breathing {
          animation: pillBreath 1s ease-in-out infinite;
        }

        .pill-split-top {
          animation: splitTop 0.8s ease-in-out forwards;
        }

        .pill-split-bottom {
          animation: splitBottom 0.8s ease-in-out forwards;
        }

        .search-expand {
          animation: searchExpand 0.8s ease-out forwards;
        }

        .fade-up {
          animation: fadeUp 0.6s ease-out forwards;
        }

        .title-fade-in {
          animation: titleFadeIn 0.6s ease-out forwards;
        }

        .slide-up {
          animation: slideUp 0.5s ease-out forwards;
        }

        .cursor-blink::after {
          content: "|";
          animation: blink 1s step-end infinite;
          color: #52B788;
          font-weight: 300;
        }

        .search-glow:focus-within {
          box-shadow: 0 0 0 4px rgba(82, 183, 136, 0.15), 0 4px 20px rgba(82, 183, 136, 0.1);
        }

        .category-pill {
          transition: all 0.2s ease;
        }
        .category-pill:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(82, 183, 136, 0.2);
        }

        @media (max-width: 640px) {
          .pill-shape { width: 200px !important; height: 72px !important; }
          .search-bar-anim { animation: none !important; width: 100% !important; }
        }
      `}</style>

      <div className="min-h-screen bg-white overflow-hidden relative" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        {/* Floating decorative pills */}
        {FLOATING_PILLS.map((p, i) => (
          <div
            key={i}
            className="absolute rounded-full pointer-events-none"
            style={{
              width: p.w,
              height: p.h,
              left: `${p.x}%`,
              top: `${p.y}%`,
              background: "linear-gradient(180deg, #52B788 50%, #ffffff 50%)",
              border: "1px solid rgba(82, 183, 136, 0.15)",
              opacity: 0.06,
              "--pill-rot": `${p.rot}deg`,
              transform: `rotate(${p.rot}deg)`,
              animation: isReady ? `floatPill ${p.dur}s ease-in-out ${p.delay}s infinite` : "none",
            }}
          />
        ))}

        <div className="relative z-10 flex flex-col items-center justify-center min-h-screen px-4">
          {/* -------- PILL ANIMATION (phases: entry, breathing, splitting) -------- */}
          {phaseIndex < 3 && (
            <div className="relative" style={{ perspective: "600px" }}>
              {/* Top half */}
              <div
                className={`pill-shape ${
                  phase === "entry" ? "pill-entrance" :
                  phase === "breathing" ? "pill-breathing" :
                  phase === "splitting" ? "pill-split-top" : ""
                }`}
                style={{
                  width: 280,
                  height: 50,
                  background: "linear-gradient(180deg, #52B788 0%, #3DA576 100%)",
                  borderRadius: "140px 140px 0 0",
                  position: "relative",
                  boxShadow: "0 20px 60px rgba(82, 183, 136, 0.3)",
                  transformOrigin: "center bottom",
                }}
              >
                {/* Glossy highlight */}
                <div
                  style={{
                    position: "absolute",
                    top: 8,
                    left: 40,
                    right: 40,
                    height: 14,
                    background: "linear-gradient(180deg, rgba(255,255,255,0.4), transparent)",
                    borderRadius: "50px",
                  }}
                />
              </div>
              {/* Dividing line */}
              <div style={{ height: 2, background: "#2D6A4F", width: 280 }} />
              {/* Bottom half */}
              <div
                className={`${
                  phase === "splitting" ? "pill-split-bottom" : ""
                }`}
                style={{
                  width: 280,
                  height: 50,
                  background: "linear-gradient(180deg, #ffffff 0%, #f8fafb 100%)",
                  borderRadius: "0 0 140px 140px",
                  border: "1px solid rgba(82, 183, 136, 0.2)",
                  borderTop: "none",
                  transformOrigin: "center top",
                }}
              />
            </div>
          )}

          {/* -------- SEARCH BAR MORPH (phase: morphing → ready) -------- */}
          {phaseIndex >= 3 && (
            <div className="w-full flex flex-col items-center">
              {/* Title */}
              <div className={`text-center mb-8 ${phase === "morphing" || isReady ? "title-fade-in" : "opacity-0"}`}>
                <h1
                  className="text-5xl font-extrabold tracking-tight sm:text-5xl"
                  style={{
                    color: "#2D6A4F",
                    textShadow: `0 1px 0 #ccc, 0 2px 0 #c9c9c9, 0 3px 0 #bbb,
                      0 4px 0 #b9b9b9, 0 5px 0 #aaa,
                      0 6px 1px rgba(0,0,0,.1), 0 0 5px rgba(0,0,0,.1),
                      0 1px 3px rgba(0,0,0,.3), 0 3px 5px rgba(0,0,0,.2),
                      0 5px 10px rgba(0,0,0,.25)`,
                    fontSize: "clamp(32px, 6vw, 48px)",
                  }}
                >
                  RxBuddy
                </h1>
                <p
                  className="mt-2 font-medium"
                  style={{
                    color: "#52B788",
                    fontSize: 16,
                    letterSpacing: "3px",
                    textTransform: "uppercase",
                  }}
                >
                  Your Pocket Pharmacist
                </p>
              </div>

              {/* Search bar */}
              <div
                className={`search-bar-anim ${phase === "morphing" ? "search-expand" : ""} ${isReady ? "" : ""}`}
                style={{
                  width: isReady ? "min(600px, 90vw)" : undefined,
                  maxWidth: "90vw",
                }}
              >
                <form
                  onSubmit={(e) => { e.preventDefault(); goSearch(query); }}
                  className="search-glow flex items-center rounded-full border-2 bg-white px-4 py-3 transition-all"
                  style={{
                    borderColor: "#52B788",
                    boxShadow: "0 20px 60px rgba(82, 183, 136, 0.15)",
                    borderRadius: 50,
                  }}
                >
                  {/* Search icon */}
                  <svg className="h-5 w-5 shrink-0" style={{ color: "#52B788" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>

                  {/* Input */}
                  <div className="flex-1 mx-3 relative">
                    <input
                      ref={inputRef}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") goSearch(query); }}
                      className="w-full bg-transparent text-base text-slate-900 outline-none placeholder:text-transparent"
                      placeholder={PLACEHOLDER_TEXT}
                    />
                    {/* Typing animation placeholder (only shows when input is empty) */}
                    {isReady && !query && (
                      <div className="absolute inset-0 flex items-center pointer-events-none">
                        <span className="text-slate-400 text-base cursor-blink">{typedText}</span>
                      </div>
                    )}
                  </div>

                  {/* Mic button */}
                  <button
                    type="button"
                    onClick={toggleMic}
                    className="shrink-0 flex h-9 w-9 items-center justify-center rounded-full transition-all"
                    style={{
                      background: listening ? "#52B788" : "transparent",
                      color: listening ? "#fff" : "#52B788",
                    }}
                    aria-label="Voice input"
                  >
                    {listening ? (
                      <span className="animate-pulse text-sm font-bold">●</span>
                    ) : (
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                      </svg>
                    )}
                  </button>

                  {/* Search button (appears when ready) */}
                  {isReady && (
                    <button
                      type="submit"
                      className="shrink-0 ml-1 flex h-9 items-center gap-1.5 rounded-full px-5 text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
                      style={{ background: "#52B788" }}
                    >
                      Search
                    </button>
                  )}
                </form>

                {/* Voice error / tip */}
                {isReady && voiceError && (
                  <p className="mt-3 text-center text-sm font-medium text-rose-600">{voiceError}</p>
                )}
              </div>

              {/* -------- PAGE CONTENT (slides up when ready) -------- */}
              {isReady && (
                <div className="w-full max-w-xl mt-10">
                  {/* Stats bar */}
                  <div className="flex justify-center gap-6 sm:gap-10 slide-up" style={{ animationDelay: "0s" }}>
                    {STATS.map((s) => (
                      <div key={s.label} className="text-center">
                        <p className="text-2xl font-bold" style={{ color: "#2D6A4F" }}>{s.value}</p>
                        <p className="text-xs font-medium text-slate-500 mt-0.5">{s.label}</p>
                      </div>
                    ))}
                  </div>

                  {/* Category pills */}
                  <div className="mt-8 slide-up" style={{ animationDelay: "0.15s" }}>
                    <p className="text-center text-xs font-semibold uppercase tracking-widest text-slate-400 mb-3">
                      Popular Topics
                    </p>
                    <div className="flex flex-wrap justify-center gap-2">
                      {CATEGORIES.map((c) => (
                        <button
                          key={c}
                          type="button"
                          onClick={() => goSearch(c)}
                          className="category-pill rounded-full border bg-white px-4 py-2 text-sm font-medium text-slate-700"
                          style={{ borderColor: "#d1fae5" }}
                        >
                          {c}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Disclaimer */}
                  <div className="mt-10 slide-up" style={{ animationDelay: "0.3s" }}>
                    <p className="text-center text-xs text-slate-400 leading-relaxed max-w-md mx-auto">
                      RxBuddy provides general information only and is not a substitute for professional medical advice.
                      For emergencies, call your local emergency number.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Show pill animation center placeholder so it stays vertically centered */}
          {phaseIndex < 3 && <div />}
        </div>
      </div>
    </>
  );
}
