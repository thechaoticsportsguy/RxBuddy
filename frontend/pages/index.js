import Head from "next/head";
import dynamic from "next/dynamic";
import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BackgroundGradientAnimation } from "../components/ui/background-gradient-animation";

const Spline = dynamic(
  () => import("@splinetool/react-spline").then((mod) => mod.default),
  {
    ssr: false,
    loading: () => (
      <div
        style={{
          width: "100%",
          height: "300px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#52B788",
          fontSize: "14px",
        }}
      >
        Loading 3D scene...
      </div>
    ),
  },
);

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

const STEPS = [
  { num: "1", title: "Ask a Question", desc: "Type or speak any medication question in plain English." },
  { num: "2", title: "Get an Answer", desc: "Our AI pharmacist analyzes FDA data and clinical guidelines." },
  { num: "3", title: "Stay Safe", desc: "See warnings, alternatives, and when to call a doctor." },
];

const EXAMPLE_QUESTIONS = [
  "Can I take ibuprofen with blood pressure meds?",
  "Is Tylenol safe during pregnancy?",
  "What are the side effects of metformin?",
  "Can I drink alcohol with amoxicillin?",
];

const PLACEHOLDER_TEXT = "Ask about any medication...";

function getSpeechRecognition() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

/* ──────────────────────────────────────────────
   Main Page
   ────────────────────────────────────────────── */
export default function HomePage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [supportsVoice, setSupportsVoice] = useState(false);
  const recognitionRef = useRef(null);
  const [typedText, setTypedText] = useState("");
  const [splineError, setSplineError] = useState(false);

  useEffect(() => {
    setSupportsVoice(!!getSpeechRecognition());
  }, []);

  useEffect(() => {
    if (!supportsVoice) return;
    const SR = getSpeechRecognition();
    if (!SR) return;

    const rec = new SR();
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

  useEffect(() => {
    let i = 0;
    const iv = setInterval(() => {
      if (i <= PLACEHOLDER_TEXT.length) {
        setTypedText(PLACEHOLDER_TEXT.slice(0, i));
        i++;
      } else {
        clearInterval(iv);
      }
    }, 50);
    return () => clearInterval(iv);
  }, []);

  const goSearch = useCallback(
    (q) => {
      const t = (q || "").trim();
      if (!t) return;
      router.push({ pathname: "/results", query: { q: t, engine: "tfidf" } });
    },
    [router],
  );

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
    try { rec.start(); setListening(true); } catch {
      setListening(false);
      setVoiceError("Could not start the microphone.");
    }
  }

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
        /* ---- Page animations ---- */
        @keyframes fadeUp {
          0%   { opacity: 0; transform: translateY(24px); }
          100% { opacity: 1; transform: translateY(0); }
        }
        @keyframes cursorBlink {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0; }
        }
        .anim-fade-up {
          animation: fadeUp 0.6s ease-out both;
        }
        .cursor-blink::after {
          content: "|";
          animation: cursorBlink 1s step-end infinite;
          color: #52B788;
        }
        .category-pill {
          transition: all 0.2s ease;
        }
        .category-pill:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 16px rgba(82, 183, 136, 0.3);
          background: rgba(255, 255, 255, 0.25) !important;
        }
        .search-glow:focus-within {
          box-shadow: 0 0 0 4px rgba(82, 183, 136, 0.25), 0 8px 30px rgba(82, 183, 136, 0.2);
          border-color: #52B788 !important;
        }
        .step-card {
          transition: all 0.2s ease;
        }
        .step-card:hover {
          transform: translateY(-4px);
          box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
        }
        .example-btn {
          transition: all 0.2s ease;
        }
        .example-btn:hover {
          background: rgba(255, 255, 255, 0.15) !important;
          border-color: rgba(82, 183, 136, 0.5) !important;
        }

      `}</style>

      <BackgroundGradientAnimation
        gradientBackgroundStart="rgb(20, 40, 20)"
        gradientBackgroundEnd="rgb(10, 25, 15)"
        firstColor="45, 106, 79"
        secondColor="82, 183, 136"
        thirdColor="27, 67, 50"
        fourthColor="183, 228, 199"
        fifthColor="52, 211, 153"
        pointerColor="82, 183, 136"
        containerClassName="min-h-screen"
      >
        <div
          className="relative z-10 flex flex-col items-center min-h-screen px-4"
          style={{ fontFamily: "'Inter', system-ui, sans-serif" }}
        >
          {/* ---- Title ---- */}
          <div className="mt-14 sm:mt-20 text-center anim-fade-up">
            <h1
              className="font-extrabold tracking-tight text-white"
              style={{
                fontSize: "clamp(40px, 8vw, 64px)",
                textShadow: "0 2px 20px rgba(82, 183, 136, 0.4)",
              }}
            >
              RxBuddy
            </h1>
            <p
              className="mt-2 font-medium uppercase"
              style={{ color: "#B7E4C7", fontSize: 15, letterSpacing: "4px" }}
            >
              Your Pocket Pharmacist
            </p>
          </div>

          <div style={{ width: "100%", maxWidth: "500px", height: "300px", margin: "0 auto" }}>
            {typeof window !== "undefined" && (
              !splineError ? (
                <Spline
                  scene="https://prod.spline.design/0fc3ccf2-6131-4754-a821-e00b70790d20/scene.splinecode"
                  onError={() => setSplineError(true)}
                />
              ) : (
                <div style={{ color: "#52B788", textAlign: "center", padding: "20px" }}>
                  💊 RxBuddy
                </div>
              )
            )}
          </div>

          {/* ---- Search Bar ---- */}
          <div className="mt-4 w-full max-w-xl anim-fade-up" style={{ animationDelay: "0.2s" }}>
            <form
              onSubmit={(e) => { e.preventDefault(); goSearch(query); }}
              className="search-glow flex items-center w-full rounded-full bg-white px-5 py-3.5 transition-all"
              style={{
                border: "2px solid rgba(45, 106, 79, 0.4)",
                boxShadow: "0 16px 48px rgba(0, 0, 0, 0.25)",
                borderRadius: 50,
              }}
            >
              <svg className="h-5 w-5 shrink-0 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>

              <div className="flex-1 mx-3 relative">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="w-full bg-transparent text-base text-slate-900 outline-none placeholder:text-transparent"
                  placeholder={PLACEHOLDER_TEXT}
                />
                {!query && (
                  <span className="absolute inset-0 flex items-center text-slate-400 text-base pointer-events-none cursor-blink">
                    {typedText}
                  </span>
                )}
              </div>

              <button
                type="button"
                onClick={toggleMic}
                className="shrink-0 flex h-9 w-9 items-center justify-center rounded-full transition-all"
                style={{
                  background: listening ? "#52B788" : "transparent",
                  color: listening ? "#fff" : "#2D6A4F",
                }}
                aria-label="Voice input"
              >
                {listening ? (
                  <span className="animate-pulse text-sm font-bold">&#9679;</span>
                ) : (
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                  </svg>
                )}
              </button>

              <button
                type="submit"
                className="shrink-0 ml-1 flex h-9 items-center gap-1.5 rounded-full px-5 text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
                style={{ background: "#2D6A4F" }}
              >
                Search
              </button>
            </form>

            {voiceError && (
              <p className="mt-3 text-center text-sm font-medium text-rose-300">{voiceError}</p>
            )}
          </div>

          {/* ---- Stats Bar ---- */}
          <div className="mt-10 flex justify-center gap-8 sm:gap-14 anim-fade-up" style={{ animationDelay: "0.35s" }}>
            {STATS.map((s) => (
              <div key={s.label} className="text-center">
                <p className="text-3xl font-bold text-white">{s.value}</p>
                <p className="text-xs font-medium mt-0.5" style={{ color: "#B7E4C7" }}>{s.label}</p>
              </div>
            ))}
          </div>

          {/* ---- Category Pills ---- */}
          <div className="mt-10 anim-fade-up" style={{ animationDelay: "0.45s" }}>
            <p className="text-center text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "#B7E4C7" }}>
              Popular Topics
            </p>
            <div className="flex flex-wrap justify-center gap-2 max-w-lg">
              {CATEGORIES.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => goSearch(c)}
                  className="category-pill rounded-full px-4 py-2 text-sm font-medium text-white"
                  style={{
                    background: "rgba(255, 255, 255, 0.1)",
                    border: "1px solid rgba(255, 255, 255, 0.2)",
                    backdropFilter: "blur(4px)",
                  }}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>

          {/* ---- How It Works ---- */}
          <div className="mt-16 w-full max-w-2xl anim-fade-up" style={{ animationDelay: "0.55s" }}>
            <p className="text-center text-xs font-semibold uppercase tracking-widest mb-6" style={{ color: "#B7E4C7" }}>
              How It Works
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {STEPS.map((s) => (
                <div
                  key={s.num}
                  className="step-card text-center rounded-xl p-5"
                  style={{
                    background: "rgba(255, 255, 255, 0.08)",
                    border: "1px solid rgba(255, 255, 255, 0.12)",
                    backdropFilter: "blur(8px)",
                  }}
                >
                  <div
                    className="mx-auto flex h-10 w-10 items-center justify-center rounded-full text-lg font-bold text-white"
                    style={{ background: "#52B788" }}
                  >
                    {s.num}
                  </div>
                  <p className="mt-3 text-sm font-semibold text-white">{s.title}</p>
                  <p className="mt-1 text-xs leading-relaxed" style={{ color: "#B7E4C7" }}>{s.desc}</p>
                </div>
              ))}
            </div>
          </div>

          {/* ---- Example Questions ---- */}
          <div className="mt-14 w-full max-w-xl anim-fade-up" style={{ animationDelay: "0.65s" }}>
            <p className="text-center text-xs font-semibold uppercase tracking-widest mb-4" style={{ color: "#B7E4C7" }}>
              Try Asking
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {EXAMPLE_QUESTIONS.map((eq) => (
                <button
                  key={eq}
                  type="button"
                  onClick={() => goSearch(eq)}
                  className="example-btn text-left rounded-lg px-4 py-3 text-sm text-white/90"
                  style={{
                    background: "rgba(255, 255, 255, 0.06)",
                    border: "1px solid rgba(255, 255, 255, 0.1)",
                  }}
                >
                  <span style={{ color: "#52B788" }} className="mr-1.5 font-semibold">Q:</span>
                  {eq}
                </button>
              ))}
            </div>
          </div>

          {/* ---- Footer Disclaimer ---- */}
          <div className="mt-16 mb-10 anim-fade-up" style={{ animationDelay: "0.75s" }}>
            <p className="text-center text-xs leading-relaxed max-w-md mx-auto" style={{ color: "rgba(183, 228, 199, 0.6)" }}>
              RxBuddy provides general information only and is not a substitute for professional medical advice. For emergencies, call your local emergency number.
            </p>
          </div>
        </div>
      </BackgroundGradientAnimation>
    </>
  );
}
