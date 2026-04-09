/**
 * ChatbotButton — floating morphing chat widget.
 *
 * Architecture (overflow contexts):
 *   .rxb-root          → fixed anchor, overflow: visible
 *   .rxb-float-wrap    → floating idle anim, overflow: visible  ← badge lives here
 *     .rxb-tooltip     → absolute, above button
 *     .rxb-badge       → absolute top-right, NEVER clipped
 *     .rxb-shell       → morphing container, overflow: hidden
 *       .rxb-btn       → button state
 *         .rxb-img-clip  → circle crop (overflow: hidden, separate from badge)
 *           img          → absolute centered at 170% — no scale() = no initial-crop bug
 *       .rxb-panel     → chat state (header / messages / input)
 */

import { useState, useEffect, useRef, useCallback } from "react";

const ROBOT_IMAGE = "/rxbuddy-robot1.png";
const API_BASE    = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const _msgStore   = {};

/** Pull the most likely drug name out of a free-text search query. */
function extractDrugName(query) {
  if (!query || !query.trim()) return null;
  const stop = new Set([
    "side","effects","dosage","dose","interactions","interaction","with","and",
    "or","can","i","take","is","are","safe","during","pregnancy","for","the",
    "a","an","of","my","about","does","how","when","mg","tablet","tablets",
    "capsule","capsules","drug","medication","medicine","help","what","use",
    "used","uses","warnings","warning","overdose","directions","more",
  ]);
  const words = query.trim().toLowerCase().split(/\s+/);
  const hit   = words.find(w => w.length > 2 && !stop.has(w));
  return hit ? hit.charAt(0).toUpperCase() + hit.slice(1) : null;
}

export default function ChatbotButton({ drugName = "your medication" }) {
  const storeKey = drugName || "__default__";

  const [isOpen,         setIsOpen]         = useState(false);
  const [isClosing,      setIsClosing]       = useState(false);
  const [contentVisible, setContentVisible]  = useState(false);
  const [messages,       setMessages]        = useState(
    () => _msgStore[storeKey] || [{
      role: "assistant",
      text: `Hi! I'm RxBuddy 👋 Ask me anything about ${drugName} — side effects, dosage, interactions, or warnings.`,
    }]
  );
  const [input,          setInput]           = useState("");
  const [isTyping,       setIsTyping]        = useState(false);
  const [tooltipVisible, setTooltipVisible]  = useState(true);
  const [liveDrug,       setLiveDrug]        = useState(drugName);

  const messagesEndRef  = useRef(null);
  const inputRef        = useRef(null);
  const tooltipTimer    = useRef(null);

  // ── Persist messages ────────────────────────────────────────
  useEffect(() => { _msgStore[storeKey] = messages; }, [messages, storeKey]);

  // ── Keep liveDrug in sync with prop ─────────────────────────
  useEffect(() => { setLiveDrug(drugName); }, [drugName]);

  // ── Watch page search input for live drug-name updates ──────
  useEffect(() => {
    const el = document.querySelector(
      'input[type="text"]:not(.rxb-input), input:not([type]):not(.rxb-input)'
    );
    if (!el) return;
    const onInput = (e) => {
      const extracted = extractDrugName(e.target.value);
      setLiveDrug(extracted || drugName);
    };
    el.addEventListener("input", onInput);
    return () => el.removeEventListener("input", onInput);
  }, [drugName]);

  // ── Auto-hide tooltip after 5 s, re-show on hover ───────────
  useEffect(() => {
    tooltipTimer.current = setTimeout(() => setTooltipVisible(false), 5000);
    return () => clearTimeout(tooltipTimer.current);
  }, []);

  const showTooltip = () => {
    clearTimeout(tooltipTimer.current);
    setTooltipVisible(true);
  };
  const scheduleHideTooltip = () => {
    tooltipTimer.current = setTimeout(() => setTooltipVisible(false), 1800);
  };

  // ── Content stagger (appears after shell finishes expanding) ─
  useEffect(() => {
    if (isOpen && !isClosing) {
      const t = setTimeout(() => setContentVisible(true), 310);
      return () => clearTimeout(t);
    }
  }, [isOpen, isClosing]);

  // ── Auto-scroll ─────────────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  // ── Auto-focus ──────────────────────────────────────────────
  useEffect(() => {
    if (contentVisible) {
      const t = setTimeout(() => inputRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [contentVisible]);

  // ── Open ────────────────────────────────────────────────────
  const handleOpen = () => {
    if (isOpen) return;
    setIsClosing(false);
    setIsOpen(true);
  };

  // ── Close: content fades first, THEN shell collapses ────────
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setContentVisible(false);
    setTimeout(() => { setIsOpen(false); setIsClosing(false); }, 220);
  }, []);

  // ── Send ────────────────────────────────────────────────────
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isTyping) return;
    setInput("");
    setMessages(prev => [...prev, { role: "user", text }]);
    setIsTyping(true);
    try {
      const history = messages.filter((_, i) => i > 0).slice(-6);
      const res = await fetch(`${API_BASE}/v2/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drug_name: drugName, message: text, conversation_history: history }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMessages(prev => [...prev, {
        role: "assistant",
        text: data.reply || "Sorry, I couldn't get a response.",
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: "assistant",
        text: "Something went wrong. Please try again!",
      }]);
    } finally {
      setIsTyping(false);
    }
  }, [input, isTyping, messages, drugName]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const displayDrug  = liveDrug && liveDrug !== "your medication" ? liveDrug : null;
  const tooltipLabel = displayDrug
    ? `Ask me more about ${displayDrug}`
    : "Ask me about your medication";

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap');

        /* ═══════════════════════════════════════════════════
           ROOT — fixed anchor, always overflow: visible
           so nothing inside it can get clipped by this layer
           ═══════════════════════════════════════════════════ */
        .rxb-root {
          position: fixed;
          bottom: 28px;
          right: 28px;
          z-index: 9999;
          font-family: 'DM Sans', system-ui, sans-serif;
          /* overflow intentionally NOT set → defaults to visible */
        }

        /* ═══════════════════════════════════════════════════
           FLOAT WRAPPER
           - Sized to the button (120 × 120)
           - overflow: visible → badge & tooltip are never clipped
           - Carries the entrance bounce + idle float animation
           ═══════════════════════════════════════════════════ */
        .rxb-float-wrap {
          position: relative;
          width: 120px;
          height: 120px;
          overflow: visible;
          /* Entrance bounce then idle float */
          animation:
            rxb-bounceIn 0.55s cubic-bezier(0.34, 1.45, 0.64, 1) both,
            rxb-float    3.6s ease-in-out 0.65s infinite;
        }

        /* Stop floating when chat is open */
        .rxb-float-wrap.is-open {
          animation: none;
        }

        @keyframes rxb-bounceIn {
          0%   { opacity: 0; transform: scale(0.35); }
          60%  { opacity: 1; transform: scale(1.07); }
          100% { opacity: 1; transform: scale(1); }
        }

        @keyframes rxb-float {
          0%, 100% { transform: translateY(0px);  }
          50%       { transform: translateY(-8px); }
        }

        /* ═══════════════════════════════════════════════════
           TOOLTIP
           Floats above the button. Pointer arrow at bottom-right.
           ═══════════════════════════════════════════════════ */
        .rxb-tooltip {
          position: absolute;
          bottom: calc(100% + 14px);
          right: 0;
          background: #fff;
          color: #111827;
          font-size: 13px;
          font-weight: 500;
          line-height: 1.4;
          padding: 9px 14px;
          border-radius: 12px;
          white-space: nowrap;
          box-shadow:
            0 4px 20px rgba(0,0,0,0.10),
            0 1px  4px rgba(0,0,0,0.06);
          pointer-events: none;
          animation: rxb-tooltipIn 280ms cubic-bezier(0.34, 1.2, 0.64, 1) both;
        }

        /* Down-pointing caret aligned to button */
        .rxb-tooltip::after {
          content: '';
          position: absolute;
          top: 100%;
          right: 22px;
          border: 7px solid transparent;
          border-top-color: #fff;
        }

        @keyframes rxb-tooltipIn {
          from { opacity: 0; transform: translateY(6px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0)   scale(1); }
        }

        /* ═══════════════════════════════════════════════════
           BADGE
           Sibling of .rxb-shell — lives OUTSIDE overflow:hidden.
           Positioned relative to .rxb-float-wrap (overflow:visible).
           ═══════════════════════════════════════════════════ */
        .rxb-badge {
          position: absolute;
          top: -6px;
          right: -6px;
          z-index: 10;           /* above shell */
          width: 26px;
          height: 26px;
          border-radius: 50%;
          background: #ef4444;
          color: #fff;
          font-size: 13px;
          font-weight: 700;
          display: flex;
          align-items: center;
          justify-content: center;
          border: 2.5px solid #fff;
          /* Pop in after entrance */
          opacity: 0;
          animation: rxb-badgePop 0.38s cubic-bezier(0.34, 1.5, 0.64, 1) 0.55s both;
          pointer-events: none;
        }

        @keyframes rxb-badgePop {
          from { opacity: 0; transform: scale(0.4); }
          to   { opacity: 1; transform: scale(1);   }
        }

        /* ═══════════════════════════════════════════════════
           MORPHING SHELL
           overflow: hidden handles the circular crop (button state)
           and the chat-panel clipping (open state).
           Badge is NOT inside here so it won't be clipped.
           ═══════════════════════════════════════════════════ */
        .rxb-shell {
          position: absolute;   /* inside float-wrap */
          bottom: 0;
          right: 0;
          width: 120px;
          height: 120px;
          border-radius: 50%;
          background: transparent;
          overflow: hidden;     /* ← circle crop for img & chat panel */
          cursor: pointer;
          transform-origin: bottom right;
          transition:
            width        460ms cubic-bezier(0.34, 1.12, 0.64, 1),
            height       460ms cubic-bezier(0.34, 1.12, 0.64, 1),
            border-radius 460ms cubic-bezier(0.34, 1.12, 0.64, 1),
            box-shadow   300ms ease;
          will-change: width, height, border-radius;
        }

        .rxb-shell.open {
          width: 360px;
          height: 520px;
          border-radius: 20px;
          box-shadow:
            0 24px 64px rgba(0,0,0,0.20),
            0  4px 16px rgba(0,0,0,0.10);
          cursor: default;
        }

        @media (max-width: 480px) {
          .rxb-root { bottom: 16px; right: 16px; }
          .rxb-shell.open { width: calc(100vw - 32px); height: 480px; }
        }

        /* ═══════════════════════════════════════════════════
           BUTTON STATE (visible when shell is closed)
           ═══════════════════════════════════════════════════ */
        .rxb-btn {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: opacity 200ms ease, transform 200ms ease;
        }

        .rxb-btn.hidden {
          opacity: 0;
          pointer-events: none;
          transform: scale(0.55);
        }

        /* ── Image clip circle ────────────────────────────── */
        /* Separate inner wrapper so only the image is cropped,
           not the badge. overflow:hidden here is intentional.  */
        .rxb-img-clip {
          width: 120px;
          height: 120px;
          border-radius: 50%;
          overflow: hidden;
          position: relative;
          flex-shrink: 0;
        }

        /*
          KEY FIX for initial-render cropping:
          Instead of transform:scale() (which can glitch on first paint),
          we use absolute centering at 170% size.
          Result: robot's own black circle fills the clip exactly,
          white padding is cropped, no scale() means no animation conflict.
        */
        .rxb-img-clip img {
          position: absolute;
          top: 50%;
          left: 50%;
          width: 170%;
          height: 170%;
          transform: translate(-50%, -50%);
          object-fit: cover;
          display: block;
          pointer-events: none;
          user-select: none;
          -webkit-user-drag: none;
          /* Smooth hover zoom */
          transition: width 180ms ease, height 180ms ease;
        }

        /* Hover: expand slightly */
        .rxb-shell:not(.open):hover .rxb-img-clip img {
          width: 180%;
          height: 180%;
        }

        /* ═══════════════════════════════════════════════════
           CHAT PANEL (visible when shell is open)
           ═══════════════════════════════════════════════════ */
        .rxb-panel {
          position: absolute;
          inset: 0;
          display: flex;
          flex-direction: column;
          opacity: 0;
          pointer-events: none;
          transition: opacity 180ms ease;
        }

        .rxb-panel.visible {
          opacity: 1;
          pointer-events: all;
        }

        /* ── Header ─────────────────────────────────────── */
        .rxb-header {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 14px 16px;
          background: #111827;
          border-radius: 20px 20px 0 0;
          flex-shrink: 0;
          transform: translateY(-6px);
          opacity: 0;
          transition: transform 240ms ease, opacity 240ms ease;
        }
        .rxb-panel.visible .rxb-header {
          transform: translateY(0);
          opacity: 1;
          transition-delay: 40ms;
        }

        .rxb-header-dot {
          width: 9px; height: 9px;
          border-radius: 50%;
          background: #22c55e;
          flex-shrink: 0;
          box-shadow: 0 0 6px rgba(34,197,94,.55);
        }
        .rxb-header-text  { flex: 1; min-width: 0; }
        .rxb-header-title {
          font-size: 14px; font-weight: 600;
          color: #fff; line-height: 1.2;
        }
        .rxb-header-sub {
          font-size: 11px; color: #9ca3af;
          line-height: 1.2; margin-top: 2px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          text-transform: capitalize;
        }
        .rxb-close {
          background: none; border: none;
          color: #9ca3af; cursor: pointer;
          padding: 4px 6px; border-radius: 6px;
          display: flex; align-items: center; justify-content: center;
          transition: color 140ms ease, background 140ms ease;
          font-size: 18px; line-height: 1; flex-shrink: 0;
        }
        .rxb-close:hover { color: #fff; background: rgba(255,255,255,.08); }

        /* ── Messages ───────────────────────────────────── */
        .rxb-messages {
          flex: 1; overflow-y: auto;
          padding: 14px 14px 8px;
          background: #111827;
          display: flex; flex-direction: column; gap: 10px;
          transform: translateY(8px); opacity: 0;
          transition: transform 260ms ease, opacity 260ms ease;
        }
        .rxb-panel.visible .rxb-messages {
          transform: translateY(0); opacity: 1;
          transition-delay: 80ms;
        }
        .rxb-messages::-webkit-scrollbar       { width: 4px; }
        .rxb-messages::-webkit-scrollbar-track { background: transparent; }
        .rxb-messages::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }

        .rxb-bubble {
          max-width: 85%; padding: 10px 13px;
          border-radius: 14px;
          font-size: 13px; line-height: 1.55;
          word-break: break-word;
          animation: rxb-bubbleIn 200ms ease forwards;
        }
        @keyframes rxb-bubbleIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .rxb-bubble.assistant {
          background: #1f2937; color: #e5e7eb;
          border-bottom-left-radius: 4px; align-self: flex-start;
        }
        .rxb-bubble.user {
          background: #059669; color: #fff;
          border-bottom-right-radius: 4px; align-self: flex-end;
        }

        /* ── Typing indicator ───────────────────────────── */
        .rxb-typing {
          display: flex; align-items: center; gap: 4px;
          padding: 10px 14px;
          background: #1f2937;
          border-radius: 14px; border-bottom-left-radius: 4px;
          align-self: flex-start; width: 52px;
        }
        .rxb-typing span {
          width: 6px; height: 6px;
          background: #6b7280; border-radius: 50%;
          animation: rxb-typingDot 1.2s ease infinite;
        }
        .rxb-typing span:nth-child(2) { animation-delay: .18s; }
        .rxb-typing span:nth-child(3) { animation-delay: .36s; }
        @keyframes rxb-typingDot {
          0%,60%,100% { transform: translateY(0);    opacity: .4; }
          30%          { transform: translateY(-4px); opacity: 1;  }
        }

        /* ── Input area ─────────────────────────────────── */
        .rxb-input-area {
          display: flex; align-items: center; gap: 8px;
          padding: 12px 14px;
          background: #111827;
          border-top: 1px solid #1f2937;
          border-radius: 0 0 20px 20px;
          flex-shrink: 0;
          transform: translateY(8px); opacity: 0;
          transition: transform 260ms ease, opacity 260ms ease;
        }
        .rxb-panel.visible .rxb-input-area {
          transform: translateY(0); opacity: 1;
          transition-delay: 120ms;
        }
        .rxb-input {
          flex: 1; min-width: 0;
          background: #1f2937;
          border: 1px solid #374151; border-radius: 10px;
          padding: 9px 13px;
          font-size: 13px; color: #e5e7eb;
          font-family: 'DM Sans', system-ui, sans-serif;
          outline: none;
          transition: border-color 150ms ease;
        }
        .rxb-input::placeholder { color: #6b7280; }
        .rxb-input:focus         { border-color: #059669; }

        .rxb-send {
          width: 36px; height: 36px;
          border-radius: 10px; background: #059669;
          border: none; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0;
          transition: background 140ms ease, transform 100ms ease;
        }
        .rxb-send:hover:not(:disabled)  { background: #047857; }
        .rxb-send:active:not(:disabled) { transform: scale(0.91); }
        .rxb-send:disabled { background: #374151; cursor: not-allowed; }
        .rxb-send svg { width: 15px; height: 15px; fill: #fff; }
      `}</style>

      <div className="rxb-root">

        {/* ── Float wrapper (badge + shell, overflow:visible) ── */}
        <div
          className={`rxb-float-wrap${isOpen ? " is-open" : ""}`}
          onMouseEnter={showTooltip}
          onMouseLeave={scheduleHideTooltip}
        >
          {/* Tooltip — above the button */}
          {!isOpen && tooltipVisible && (
            <div className="rxb-tooltip" role="tooltip">
              {tooltipLabel}
            </div>
          )}

          {/* Badge — sibling to shell, never clipped */}
          {!isOpen && (
            <div className="rxb-badge" aria-label="Chat available">?</div>
          )}

          {/* Morphing shell */}
          <div
            className={`rxb-shell${isOpen ? " open" : ""}`}
            onClick={!isOpen ? handleOpen : undefined}
            role={isOpen ? "dialog" : "button"}
            aria-label={isOpen ? "RxBuddy chat" : "Open RxBuddy chat"}
            aria-expanded={isOpen}
          >
            {/* ── Button state ── */}
            <div className={`rxb-btn${isOpen ? " hidden" : ""}`}>
              {/*
                .rxb-img-clip: isolated overflow:hidden circle crop.
                img: absolute 170% centered — renders correctly on
                first paint, no scale() interaction with parent anims.
              */}
              <div className="rxb-img-clip">
                <img src={ROBOT_IMAGE} alt="RxBuddy assistant" />
              </div>
            </div>

            {/* ── Chat panel ── */}
            <div className={`rxb-panel${contentVisible ? " visible" : ""}`}>

              <div className="rxb-header">
                <div className="rxb-header-dot" aria-hidden="true" />
                <div className="rxb-header-text">
                  <div className="rxb-header-title">Ask RxBuddy</div>
                  <div className="rxb-header-sub">About {drugName}</div>
                </div>
                <button className="rxb-close" onClick={handleClose} aria-label="Close chat">
                  ✕
                </button>
              </div>

              <div className="rxb-messages" role="log" aria-live="polite">
                {messages.map((m, i) => (
                  <div key={i} className={`rxb-bubble ${m.role}`}>{m.text}</div>
                ))}
                {isTyping && (
                  <div className="rxb-typing" aria-label="RxBuddy is typing">
                    <span /><span /><span />
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              <div className="rxb-input-area">
                <input
                  ref={inputRef}
                  className="rxb-input"
                  placeholder={`Ask about ${drugName}…`}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  aria-label="Type your question"
                />
                <button
                  className="rxb-send"
                  onClick={handleSend}
                  disabled={!input.trim() || isTyping}
                  aria-label="Send message"
                >
                  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                  </svg>
                </button>
              </div>

            </div>
          </div>
        </div>
      </div>
    </>
  );
}
