import Head from "next/head";
import { useRouter } from "next/router";
import { useCallback, useEffect, useRef, useState } from "react";

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

export default function HomePage() {
  const router = useRouter();
  const canvasRef = useRef(null);
  const sceneRef = useRef(null);
  const animFrameRef = useRef(null);

  const [query, setQuery] = useState("");
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [supportsVoice, setSupportsVoice] = useState(false);
  const recognitionRef = useRef(null);

  const [animPhase, setAnimPhase] = useState("intro");
  const [showUI, setShowUI] = useState(false);
  const [showContent, setShowContent] = useState(false);
  const [typedText, setTypedText] = useState("");

  // ---------- Voice recognition ----------
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

  // ---------- Typing animation ----------
  useEffect(() => {
    if (!showUI) return;
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
  }, [showUI]);

  // ---------- Three.js scene ----------
  useEffect(() => {
    if (typeof window === "undefined") return;

    let THREE;
    try {
      THREE = require("three");
    } catch {
      setAnimPhase("done");
      setShowUI(true);
      setTimeout(() => setShowContent(true), 300);
      return;
    }

    const canvas = canvasRef.current;
    if (!canvas) return;

    const container = canvas.parentElement;
    let width = container?.clientWidth || window.innerWidth;
    let height = container?.clientHeight || window.innerHeight;
    const isMobile = width < 768;

    // --- Renderer ---
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;

    // --- Scene ---
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf0f4f0);

    // --- Camera ---
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, 0, 8);

    // --- Lights ---
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
    keyLight.position.set(5, 8, 5);
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0xb7e4c7, 0.8);
    fillLight.position.set(-5, 2, 3);
    scene.add(fillLight);

    const rimLight = new THREE.DirectionalLight(0xffffff, 1.5);
    rimLight.position.set(0, -5, -5);
    scene.add(rimLight);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0xffffff, 2.0, 10);
    pointLight.position.set(2, 4, 4);
    scene.add(pointLight);

    // --- Materials ---
    const greenMat = new THREE.MeshPhysicalMaterial({
      color: 0x52b788,
      metalness: 0.1,
      roughness: 0.05,
      clearcoat: 1.0,
      clearcoatRoughness: 0.05,
      reflectivity: 1.0,
    });

    const whiteMat = new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      metalness: 0.1,
      roughness: 0.05,
      clearcoat: 1.0,
      clearcoatRoughness: 0.05,
      reflectivity: 1.0,
    });

    // --- Build capsule halves ---
    // Green half: left hemisphere + left half of cylinder
    const greenGroup = new THREE.Group();

    const leftSphereGeo = new THREE.SphereGeometry(1, 64, 64, Math.PI, Math.PI);
    const leftSphere = new THREE.Mesh(leftSphereGeo, greenMat);
    leftSphere.position.y = 1;
    greenGroup.add(leftSphere);

    const leftCylGeo = new THREE.CylinderGeometry(1, 1, 2, 64, 1, true, Math.PI, Math.PI);
    const leftCyl = new THREE.Mesh(leftCylGeo, greenMat);
    greenGroup.add(leftCyl);

    // Cap the green half so it looks solid
    const greenCapGeo = new THREE.CircleGeometry(1, 64);
    const greenCapTop = new THREE.Mesh(greenCapGeo, greenMat);
    greenCapTop.position.y = 1;
    greenCapTop.rotation.x = -Math.PI / 2;
    greenGroup.add(greenCapTop);

    const greenCapBot = new THREE.Mesh(greenCapGeo.clone(), greenMat);
    greenCapBot.position.y = -1;
    greenCapBot.rotation.x = Math.PI / 2;
    greenGroup.add(greenCapBot);

    // White half: right hemisphere + right half of cylinder
    const whiteGroup = new THREE.Group();

    const rightSphereGeo = new THREE.SphereGeometry(1, 64, 64, 0, Math.PI);
    const rightSphere = new THREE.Mesh(rightSphereGeo, whiteMat);
    rightSphere.position.y = -1;
    whiteGroup.add(rightSphere);

    const rightCylGeo = new THREE.CylinderGeometry(1, 1, 2, 64, 1, true, 0, Math.PI);
    const rightCyl = new THREE.Mesh(rightCylGeo, whiteMat);
    whiteGroup.add(rightCyl);

    const whiteCapGeo = new THREE.CircleGeometry(1, 64);
    const whiteCapTop = new THREE.Mesh(whiteCapGeo, whiteMat);
    whiteCapTop.position.y = 1;
    whiteCapTop.rotation.x = -Math.PI / 2;
    whiteGroup.add(whiteCapTop);

    const whiteCapBot = new THREE.Mesh(whiteCapGeo.clone(), whiteMat);
    whiteCapBot.position.y = -1;
    whiteCapBot.rotation.x = Math.PI / 2;
    whiteGroup.add(whiteCapBot);

    // "RxBuddy" text on white half via canvas texture
    const textCanvas = document.createElement("canvas");
    textCanvas.width = 512;
    textCanvas.height = 256;
    const ctx = textCanvas.getContext("2d");
    ctx.fillStyle = "rgba(255,255,255,0)";
    ctx.fillRect(0, 0, 512, 256);
    ctx.fillStyle = "#2D6A4F";
    ctx.font = "bold 64px Inter, Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("RxBuddy", 256, 128);

    const textTexture = new THREE.CanvasTexture(textCanvas);
    textTexture.needsUpdate = true;

    const textMat = new THREE.MeshBasicMaterial({
      map: textTexture,
      transparent: true,
      depthWrite: false,
    });

    const textPlaneGeo = new THREE.PlaneGeometry(2.4, 1.2);
    const textPlane = new THREE.Mesh(textPlaneGeo, textMat);
    textPlane.position.set(0, -0.6, 1.01);
    textPlane.rotation.x = 0;
    whiteGroup.add(textPlane);

    // Pill group: rotate so horizontal
    const pill = new THREE.Group();
    pill.add(greenGroup);
    pill.add(whiteGroup);
    pill.rotation.z = Math.PI / 2;

    const pillScale = isMobile ? 1.8 : 3;
    pill.scale.set(0.01, 0.01, 0.01);
    scene.add(pill);

    sceneRef.current = { scene, camera, renderer, pill, greenGroup, whiteGroup, pillScale };

    // --- Animation ---
    const startTime = performance.now();
    let phase = "intro"; // intro → idle → splitting → done

    function animate() {
      animFrameRef.current = requestAnimationFrame(animate);

      const elapsed = performance.now() - startTime;

      if (phase === "intro") {
        const target = new THREE.Vector3(pillScale, pillScale, pillScale);
        pill.scale.lerp(target, 0.06);
        pill.rotation.y += 0.01;

        if (elapsed > 1000) {
          phase = "idle";
          setAnimPhase("idle");
        }
      }

      if (phase === "idle") {
        pill.rotation.y += 0.008;
        pill.position.y = Math.sin(elapsed * 0.001) * 0.1;

        if (elapsed > 3000) {
          phase = "splitting";
          setAnimPhase("splitting");
          greenMat.transparent = true;
          whiteMat.transparent = true;
          textMat.transparent = true;
        }
      }

      if (phase === "splitting") {
        greenGroup.position.y += 0.06;
        greenMat.opacity = Math.max(0, greenMat.opacity - 0.015);

        whiteGroup.position.y -= 0.06;
        whiteMat.opacity = Math.max(0, whiteMat.opacity - 0.015);
        textMat.opacity = Math.max(0, textMat.opacity - 0.02);

        if (greenMat.opacity <= 0 && whiteMat.opacity <= 0) {
          phase = "done";
          setAnimPhase("done");
          setShowUI(true);
          setTimeout(() => setShowContent(true), 300);
        }
      }

      renderer.render(scene, camera);
    }

    animate();

    // --- Resize ---
    function onResize() {
      width = container?.clientWidth || window.innerWidth;
      height = container?.clientHeight || window.innerHeight;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    }
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      renderer.dispose();
      greenMat.dispose();
      whiteMat.dispose();
      textMat.dispose();
      textTexture.dispose();
    };
  }, []);

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
        @keyframes searchExpand {
          0%   { max-width: 0; opacity: 0; padding: 0; }
          100% { max-width: 640px; opacity: 1; }
        }
        @keyframes fadeUp {
          0%   { opacity: 0; transform: translateY(24px); }
          100% { opacity: 1; transform: translateY(0); }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0; }
        }
        .anim-search-expand {
          animation: searchExpand 0.7s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        .anim-fade-up {
          animation: fadeUp 0.5s ease-out both;
        }
        .cursor-blink::after {
          content: "|";
          animation: blink 1s step-end infinite;
          color: #52B788;
        }
        .category-pill {
          transition: all 0.2s ease;
        }
        .category-pill:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(82, 183, 136, 0.2);
        }
        .search-glow:focus-within {
          box-shadow: 0 0 0 4px rgba(82,183,136,0.15), 0 4px 20px rgba(82,183,136,0.1);
        }
      `}</style>

      <div className="min-h-screen bg-white relative overflow-hidden" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
        {/* Three.js canvas — visible during 3D animation */}
        <div
          className="absolute inset-0 z-0"
          style={{ display: animPhase === "done" ? "none" : "block" }}
        >
          <canvas ref={canvasRef} className="w-full h-full" />
        </div>

        {/* ---- UI after animation ---- */}
        {showUI && (
          <div className="relative z-10 flex flex-col items-center min-h-screen" style={{ background: "linear-gradient(180deg, #f0f4f0 0%, #ffffff 40%)" }}>
            {/* Title */}
            <div className="mt-16 sm:mt-24 text-center anim-fade-up">
              <h1
                className="font-extrabold tracking-tight"
                style={{
                  color: "#2D6A4F",
                  fontSize: "clamp(36px, 7vw, 56px)",
                  textShadow: `0 1px 0 #ccc, 0 2px 0 #c9c9c9, 0 3px 0 #bbb,
                    0 4px 0 #b9b9b9, 0 5px 0 #aaa,
                    0 6px 1px rgba(0,0,0,.1), 0 0 5px rgba(0,0,0,.1),
                    0 1px 3px rgba(0,0,0,.3), 0 3px 5px rgba(0,0,0,.2),
                    0 5px 10px rgba(0,0,0,.25)`,
                }}
              >
                RxBuddy
              </h1>
              <p
                className="mt-2 font-medium uppercase"
                style={{ color: "#52B788", fontSize: 15, letterSpacing: "3px" }}
              >
                Your Pocket Pharmacist
              </p>
            </div>

            {/* Search bar */}
            <div className="mt-8 w-full px-4 flex justify-center anim-search-expand">
              <form
                onSubmit={(e) => { e.preventDefault(); goSearch(query); }}
                className="search-glow flex items-center w-full rounded-full border-2 bg-white px-4 py-3 transition-all"
                style={{
                  maxWidth: 640,
                  borderColor: "#52B788",
                  boxShadow: "0 16px 48px rgba(82,183,136,0.13)",
                  borderRadius: 50,
                }}
              >
                <svg className="h-5 w-5 shrink-0" style={{ color: "#52B788" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
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
                  style={{ background: listening ? "#52B788" : "transparent", color: listening ? "#fff" : "#52B788" }}
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
                  style={{ background: "#52B788" }}
                >
                  Search
                </button>
              </form>
            </div>

            {voiceError && (
              <p className="mt-2 text-center text-sm font-medium text-rose-600">{voiceError}</p>
            )}

            {/* Below-fold content */}
            {showContent && (
              <div className="w-full max-w-3xl px-4 mt-10">
                {/* Stats */}
                <div className="flex justify-center gap-8 sm:gap-14 anim-fade-up" style={{ animationDelay: "0s" }}>
                  {STATS.map((s) => (
                    <div key={s.label} className="text-center">
                      <p className="text-3xl font-bold" style={{ color: "#2D6A4F" }}>{s.value}</p>
                      <p className="text-xs font-medium text-slate-500 mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>

                {/* Categories */}
                <div className="mt-10 anim-fade-up" style={{ animationDelay: "0.1s" }}>
                  <p className="text-center text-xs font-semibold uppercase tracking-widest text-slate-400 mb-3">Popular Topics</p>
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

                {/* How it works */}
                <div className="mt-14 anim-fade-up" style={{ animationDelay: "0.2s" }}>
                  <p className="text-center text-xs font-semibold uppercase tracking-widest text-slate-400 mb-6">How It Works</p>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
                    {STEPS.map((s) => (
                      <div key={s.num} className="text-center rounded-xl border border-slate-100 bg-white p-5 shadow-sm">
                        <div
                          className="mx-auto flex h-10 w-10 items-center justify-center rounded-full text-lg font-bold text-white"
                          style={{ background: "#52B788" }}
                        >
                          {s.num}
                        </div>
                        <p className="mt-3 text-sm font-semibold text-slate-800">{s.title}</p>
                        <p className="mt-1 text-xs text-slate-500 leading-relaxed">{s.desc}</p>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Example questions */}
                <div className="mt-14 anim-fade-up" style={{ animationDelay: "0.3s" }}>
                  <p className="text-center text-xs font-semibold uppercase tracking-widest text-slate-400 mb-4">Try Asking</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {EXAMPLE_QUESTIONS.map((eq) => (
                      <button
                        key={eq}
                        type="button"
                        onClick={() => goSearch(eq)}
                        className="text-left rounded-lg border border-slate-100 bg-white px-4 py-3 text-sm text-slate-700 transition-all hover:border-emerald-200 hover:shadow-sm"
                      >
                        <span style={{ color: "#52B788" }} className="mr-1.5 font-medium">Q:</span>
                        {eq}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Disclaimer footer */}
                <div className="mt-14 mb-10 anim-fade-up" style={{ animationDelay: "0.4s" }}>
                  <p className="text-center text-xs text-slate-400 leading-relaxed max-w-md mx-auto">
                    RxBuddy provides general information only and is not a substitute for professional medical advice. For emergencies, call your local emergency number.
                  </p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
