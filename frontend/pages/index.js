import Head from "next/head";
import dynamic from "next/dynamic";
import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BackgroundGradientAnimation } from "../components/ui/background-gradient-animation";

const Spline = dynamic(() => import("@splinetool/react-spline"), {
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
      }}
    >
      Loading 3D scene...
    </div>
  ),
});

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
   PharmacistRobot — pure CSS animated character
   ────────────────────────────────────────────── */
function PharmacistRobot() {
  const [hovered, setHovered] = useState(false);
  const [clicked, setClicked] = useState(false);

  function handleClick() {
    setClicked(true);
    setTimeout(() => setClicked(false), 1200);
  }

  return (
    <div
      className="robot-wrap"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={handleClick}
      style={{ cursor: "pointer", position: "relative", display: "inline-block" }}
    >
      {/* Speech bubble */}
      <div
        className="speech-bubble"
        style={{
          position: "absolute",
          top: -48,
          left: "50%",
          transform: "translateX(-50%)",
          background: "rgba(255,255,255,0.95)",
          color: "#2D6A4F",
          fontSize: 11,
          fontWeight: 600,
          padding: "6px 14px",
          borderRadius: 12,
          whiteSpace: "nowrap",
          opacity: hovered || clicked ? 1 : 0,
          transition: "opacity 0.3s ease, transform 0.3s ease",
          pointerEvents: "none",
          boxShadow: "0 4px 16px rgba(0,0,0,0.15)",
          zIndex: 20,
        }}
      >
        {clicked ? "Let's find your answer! 🔍" : "Ask me about your medications! 💊"}
        <div style={{
          position: "absolute",
          bottom: -6,
          left: "50%",
          transform: "translateX(-50%)",
          width: 0,
          height: 0,
          borderLeft: "6px solid transparent",
          borderRight: "6px solid transparent",
          borderTop: "6px solid rgba(255,255,255,0.95)",
        }} />
      </div>

      {/* Robot container */}
      <div
        className={`robot-body ${clicked ? "robot-bounce" : ""}`}
        style={{
          width: 140,
          height: 220,
          position: "relative",
          animation: hovered ? "float 1.5s ease-in-out infinite" : "float 3s ease-in-out infinite",
          transition: "filter 0.3s ease",
          filter: hovered
            ? "drop-shadow(0 0 24px rgba(82,183,136,0.6))"
            : "drop-shadow(0 0 12px rgba(82,183,136,0.3))",
        }}
      >
        {/* ---- ANTENNA LEFT ---- */}
        <div style={{
          position: "absolute", top: 4, left: 42,
          width: 3, height: 18, background: "#52B788", borderRadius: 2,
          transform: "rotate(-15deg)", transformOrigin: "bottom center",
        }}>
          <div style={{
            position: "absolute", top: -5, left: -3,
            width: 9, height: 9, borderRadius: "50%", background: "#fff",
            boxShadow: "0 0 6px rgba(82,183,136,0.5)",
          }} />
        </div>
        {/* ---- ANTENNA RIGHT ---- */}
        <div style={{
          position: "absolute", top: 4, right: 42,
          width: 3, height: 18, background: "#52B788", borderRadius: 2,
          transform: "rotate(15deg)", transformOrigin: "bottom center",
        }}>
          <div style={{
            position: "absolute", top: -5, left: -3,
            width: 9, height: 9, borderRadius: "50%", background: "#fff",
            boxShadow: "0 0 6px rgba(82,183,136,0.5)",
          }} />
        </div>

        {/* ---- HEAD ---- */}
        <div style={{
          position: "absolute", top: 18, left: 20, width: 100, height: 72,
          background: "#fff", borderRadius: 20,
          boxShadow: "0 4px 20px rgba(0,0,0,0.1)",
          border: "2px solid rgba(82,183,136,0.2)",
          overflow: "hidden",
        }}>
          {/* Eyes */}
          <div style={{ position: "absolute", top: 20, left: 18, display: "flex", gap: 24 }}>
            {/* Left eye */}
            <div className="robot-eye" style={{
              width: hovered ? 18 : 16, height: hovered ? 22 : 20,
              background: "#2D6A4F", borderRadius: "50%",
              position: "relative", transition: "all 0.2s ease",
              animation: "eyeBlink 3s ease-in-out infinite",
            }}>
              <div style={{
                position: "absolute", top: 4, left: 4,
                width: 6, height: 6, borderRadius: "50%", background: "#fff",
              }} />
            </div>
            {/* Right eye */}
            <div className="robot-eye" style={{
              width: hovered ? 18 : 16, height: hovered ? 22 : 20,
              background: "#2D6A4F", borderRadius: "50%",
              position: "relative", transition: "all 0.2s ease",
              animation: "eyeBlink 3s ease-in-out infinite",
            }}>
              <div style={{
                position: "absolute", top: 4, left: 4,
                width: 6, height: 6, borderRadius: "50%", background: "#fff",
              }} />
            </div>
          </div>
          {/* Rosy cheeks */}
          <div style={{
            position: "absolute", top: 38, left: 10,
            width: 14, height: 10, borderRadius: "50%",
            background: "rgba(255, 150, 150, 0.35)",
          }} />
          <div style={{
            position: "absolute", top: 38, right: 10,
            width: 14, height: 10, borderRadius: "50%",
            background: "rgba(255, 150, 150, 0.35)",
          }} />
          {/* Smile */}
          <div style={{
            position: "absolute", bottom: 12, left: "50%", transform: "translateX(-50%)",
            width: 22, height: 11, borderBottom: "3px solid #2D6A4F",
            borderRadius: "0 0 50% 50%",
          }} />
        </div>

        {/* ---- STETHOSCOPE ---- */}
        <div style={{
          position: "absolute", top: 85, left: "50%", transform: "translateX(-50%)",
          width: 36, height: 16,
          border: "3px solid #52B788", borderTop: "none",
          borderRadius: "0 0 18px 18px",
          zIndex: 3,
        }}>
          <div style={{
            position: "absolute", bottom: -5, left: "50%", transform: "translateX(-50%)",
            width: 10, height: 10, borderRadius: "50%",
            background: "#52B788", border: "2px solid #2D6A4F",
          }} />
        </div>

        {/* ---- BODY (lab coat) ---- */}
        <div style={{
          position: "absolute", top: 92, left: 22, width: 96, height: 80,
          background: "#ffffff", borderRadius: "16px 16px 12px 12px",
          border: "2px solid #2D6A4F",
          boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
          overflow: "hidden",
        }}>
          {/* Collar V-shape */}
          <div style={{
            position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)",
            width: 0, height: 0,
            borderLeft: "16px solid transparent",
            borderRight: "16px solid transparent",
            borderTop: "14px solid #2D6A4F",
          }} />
          {/* Pocket with cross */}
          <div style={{
            position: "absolute", top: 20, left: 8,
            width: 24, height: 18, borderRadius: 4,
            border: "1.5px solid #2D6A4F",
          }}>
            <div style={{
              position: "absolute", top: "50%", left: "50%",
              transform: "translate(-50%, -50%)",
              color: "#2D6A4F", fontSize: 12, fontWeight: 800, lineHeight: 1,
            }}>&#10010;</div>
          </div>
          {/* RXBUDDY text */}
          <div style={{
            position: "absolute", bottom: 10, left: 0, right: 0,
            textAlign: "center", color: "#2D6A4F",
            fontSize: 9, fontWeight: 800, letterSpacing: "1.5px",
          }}>RXBUDDY</div>
          {/* Center line (coat buttons) */}
          <div style={{
            position: "absolute", top: 16, left: "50%",
            transform: "translateX(-50%)",
            width: 2, height: 48, background: "rgba(45,106,79,0.2)",
          }} />
          {/* Buttons */}
          {[24, 38, 52].map((t) => (
            <div key={t} style={{
              position: "absolute", top: t, left: "50%",
              transform: "translateX(-50%)",
              width: 6, height: 6, borderRadius: "50%",
              background: "#2D6A4F",
            }} />
          ))}
        </div>

        {/* ---- LEFT ARM (waving) ---- */}
        <div style={{
          position: "absolute", top: 100, left: 2,
          width: 22, height: 58,
          transformOrigin: "top center",
          animation: "wave 2s ease-in-out infinite",
        }}>
          <div style={{
            width: 22, height: 44, background: "#fff",
            border: "2px solid #2D6A4F", borderRadius: "10px 10px 8px 8px",
          }} />
          {/* Hand */}
          <div style={{
            width: 18, height: 16, background: "#ffdbac",
            borderRadius: "50%", margin: "0 auto",
            border: "1.5px solid rgba(45,106,79,0.2)",
          }} />
        </div>

        {/* ---- RIGHT ARM ---- */}
        <div style={{
          position: "absolute", top: 100, right: 2,
          width: 22, height: 58,
        }}>
          <div style={{
            width: 22, height: 44, background: "#fff",
            border: "2px solid #2D6A4F", borderRadius: "10px 10px 8px 8px",
          }} />
          <div style={{
            width: 18, height: 16, background: "#ffdbac",
            borderRadius: "50%", margin: "0 auto",
            border: "1.5px solid rgba(45,106,79,0.2)",
          }} />
        </div>

        {/* ---- LEGS ---- */}
        <div style={{
          position: "absolute", top: 172, left: 34, display: "flex", gap: 12,
        }}>
          {/* Left leg + shoe */}
          <div>
            <div style={{
              width: 22, height: 24, background: "#fff",
              border: "2px solid rgba(45,106,79,0.3)", borderRadius: "6px 6px 4px 4px",
            }} />
            <div style={{
              width: 26, height: 12, background: "#2D6A4F",
              borderRadius: "4px 4px 8px 8px", marginLeft: -2,
            }} />
          </div>
          {/* Right leg + shoe */}
          <div>
            <div style={{
              width: 22, height: 24, background: "#fff",
              border: "2px solid rgba(45,106,79,0.3)", borderRadius: "6px 6px 4px 4px",
            }} />
            <div style={{
              width: 26, height: 12, background: "#2D6A4F",
              borderRadius: "4px 4px 8px 8px", marginLeft: -2,
            }} />
          </div>
        </div>
      </div>
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
        <title>RxBuddy — Your pocket pharmacist</title>
        <meta name="description" content="Ask everyday medication questions and get fast, plain-English help." />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      </Head>

      <style jsx global>{`
        /* ---- Robot animations ---- */
        @keyframes float {
          0%, 100% { transform: translateY(0px); }
          50%      { transform: translateY(-12px); }
        }
        @keyframes wave {
          0%, 100% { transform: rotate(-10deg); }
          50%      { transform: rotate(20deg); }
        }
        @keyframes eyeBlink {
          0%, 90%, 100% { transform: scaleY(1); }
          95%           { transform: scaleY(0.1); }
        }
        .robot-bounce {
          animation: bounce 0.4s ease !important;
        }
        @keyframes bounce {
          0%   { transform: translateY(0); }
          30%  { transform: translateY(-18px); }
          50%  { transform: translateY(0); }
          70%  { transform: translateY(-8px); }
          100% { transform: translateY(0); }
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

        /* Mobile: scale robot down */
        @media (max-width: 640px) {
          .robot-wrap {
            transform: scale(0.7);
            transform-origin: top center;
          }
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

          <div
            style={{
              width: "100%",
              maxWidth: "500px",
              height: "300px",
              margin: "0 auto",
              position: "relative",
            }}
          >
            <Spline scene="https://prod.spline.design/0fc3ccf2-6131-4754-a821-e00b70790d20/scene.splinecode" />
          </div>

          {/* ---- Robot ---- */}
          <div className="mt-6 anim-fade-up" style={{ animationDelay: "0.1s" }}>
            <PharmacistRobot />
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
