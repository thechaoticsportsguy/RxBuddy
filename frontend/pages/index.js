import Head from "next/head";
import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BackgroundGradientAnimation } from "../components/ui/background-gradient-animation";
import Disclaimer from "../components/Disclaimer";

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
  { value: "FDA Label", label: "Data Source" },
  { value: "Free", label: "Forever" },
];

const STEPS = [
  { num: "1", title: "Ask a Question", desc: "Type or speak any medication question in plain English." },
  { num: "2", title: "Get an Answer", desc: "RxBuddy retrieves drug label data from DailyMed and Drugs@FDA to help explain your question." },
  { num: "3", title: "Review & Verify", desc: "See warnings, alternatives, and when to call a doctor or pharmacist." },
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
   Pill3D — interactive 3D CSS pill
   ────────────────────────────────────────────── */
function Pill3D() {
  const [rotation, setRotation] = useState({ x: 8, y: -5 });
  const [isHovered, setIsHovered] = useState(false);
  const [isClicked, setIsClicked] = useState(false);

  function handleMouseMove(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const rotateY = ((e.clientX - centerX) / rect.width) * 30;
    const rotateX = -((e.clientY - centerY) / rect.height) * 20;
    setRotation({ x: rotateX, y: rotateY });
  }

  function handleClick() {
    setIsClicked(true);
    setTimeout(() => setIsClicked(false), 600);
  }

  return (
    <div
      className="pill3d-wrapper"
      style={{ perspective: "800px", width: "320px", margin: "30px auto", cursor: "pointer" }}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => {
        setRotation({ x: 8, y: -5 });
        setIsHovered(false);
      }}
      onMouseEnter={() => setIsHovered(true)}
      onClick={handleClick}
    >
      <div
        className={isHovered ? "pill3d-hovered" : ""}
        style={{
          width: "300px",
          height: "100px",
          borderRadius: "50px",
          display: "flex",
          transform: isClicked
            ? `rotateX(${rotation.x}deg) rotateY(360deg)`
            : `rotateX(${rotation.x}deg) rotateY(${rotation.y}deg)`,
          transition: isClicked ? "transform 0.6s ease" : "transform 0.1s ease-out",
          animation: isHovered
            ? "glowPulse 2s ease-in-out infinite"
            : "pillFloat 3s ease-in-out infinite, glowPulse 2s ease-in-out infinite",
          boxShadow: isHovered
            ? "0 30px 70px rgba(0,0,0,0.5), 0 0 50px rgba(82,183,136,0.5), inset 0 2px 4px rgba(255,255,255,0.4), inset 0 -2px 4px rgba(0,0,0,0.2)"
            : "0 25px 60px rgba(0,0,0,0.5), inset 0 2px 4px rgba(255,255,255,0.4), inset 0 -2px 4px rgba(0,0,0,0.2)",
          border: "2px solid #2D6A4F",
          overflow: "hidden",
          position: "relative",
          transformStyle: "preserve-3d",
        }}
      >
        {/* Green left half */}
        <div
          style={{
            width: "50%",
            height: "100%",
            background: "linear-gradient(180deg, #6fcf97 0%, #52B788 40%, #2D6A4F 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: "45%",
              background: "linear-gradient(180deg, rgba(255,255,255,0.35) 0%, transparent 100%)",
              borderRadius: "50px 0 0 0",
            }}
          />
          <span style={{ fontSize: "28px", zIndex: 1, color: "#fff" }}>&#10010;</span>
        </div>

        {/* Divider seam */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: "50%",
            transform: "translateX(-50%)",
            width: "2px",
            height: "100%",
            background: "linear-gradient(180deg, rgba(45,106,79,0.1) 0%, rgba(45,106,79,0.4) 50%, rgba(45,106,79,0.1) 100%)",
            zIndex: 2,
          }}
        />

        {/* White right half */}
        <div
          style={{
            width: "50%",
            height: "100%",
            background: "linear-gradient(180deg, #ffffff 0%, #f0f0f0 40%, #d8d8d8 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: "45%",
              background: "linear-gradient(180deg, rgba(255,255,255,0.6) 0%, transparent 100%)",
              borderRadius: "0 50px 0 0",
            }}
          />
          <span
            style={{
              color: "#2D6A4F",
              fontWeight: 900,
              fontSize: "13px",
              letterSpacing: "2px",
              zIndex: 1,
              fontFamily: "'Inter', system-ui, sans-serif",
            }}
          >
            RxBuddy
          </span>
        </div>

        {/* Bottom shadow line for depth */}
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: "10%",
            right: "10%",
            height: "3px",
            background: "rgba(0,0,0,0.2)",
            borderRadius: "50%",
            filter: "blur(2px)",
          }}
        />
      </div>

      {/* Ground shadow */}
      <div
        style={{
          width: isHovered ? "180px" : "200px",
          height: "20px",
          background: "radial-gradient(ellipse, rgba(0,0,0,0.3) 0%, transparent 70%)",
          margin: "10px auto 0",
          borderRadius: "50%",
          filter: "blur(4px)",
          transition: "width 0.3s ease",
        }}
      />
    </div>
  );
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
        <title>RxBuddy — Medication Information Helper</title>
        <meta name="description" content="RxBuddy helps you understand medication questions using drug label data from DailyMed and Drugs@FDA. Not a substitute for professional medical advice." />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </Head>

      <style jsx global>{`
        /* ---- Pill 3D animations ---- */
        @keyframes pillFloat {
          0%, 100% { transform: translateY(0px) rotateX(10deg) rotateY(-5deg); }
          50%      { transform: translateY(-15px) rotateX(10deg) rotateY(-5deg); }
        }
        @keyframes glowPulse {
          0%, 100% { box-shadow: 0 20px 60px rgba(0,0,0,0.4), 0 0 30px rgba(82,183,136,0.3), inset 0 2px 4px rgba(255,255,255,0.4), inset 0 -2px 4px rgba(0,0,0,0.2); }
          50%      { box-shadow: 0 20px 60px rgba(0,0,0,0.4), 0 0 60px rgba(82,183,136,0.6), inset 0 2px 4px rgba(255,255,255,0.4), inset 0 -2px 4px rgba(0,0,0,0.2); }
        }

        /* Mobile: scale pill down */
        @media (max-width: 640px) {
          .pill3d-wrapper {
            transform: scale(0.7);
            transform-origin: top center;
          }
        }

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
              Medication Information Helper
            </p>
          </div>

          {/* ---- 3D Pill ---- */}
          <div className="anim-fade-up" style={{ animationDelay: "0.1s" }}>
            <Pill3D />
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
            <Disclaimer variant="short" />
          </div>
        </div>
      </BackgroundGradientAnimation>
    </>
  );
}
