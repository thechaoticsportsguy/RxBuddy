/**
 * RxBuddyChatWidget — morphing circular button → chat panel.
 *
 * The single "shell" div transitions between a circular button and
 * the full chat panel using width/height/border-radius so the user
 * sees the button literally expand into the chat window, and
 * collapse back on close.
 *
 * API: POST /v2/chat  (backend proxy — no Anthropic key on client)
 */

import { useState, useEffect, useRef, useCallback } from "react";

const ROBOT_IMAGE = "/rxbuddy-robot1.png";
const API_BASE    = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// Survives open/close cycles within the page session
const _msgStore = {};

export default function ChatbotButton({ drugName = "your medication" }) {
  const storeKey = drugName || "__default__";

  const [isOpen,         setIsOpen]         = useState(false);
  const [isClosing,      setIsClosing]       = useState(false);
  const [contentVisible, setContentVisible]  = useState(false);
  const [messages,       setMessages]        = useState(
    () => _msgStore[storeKey] || [
      { role: "assistant", text: `Hi! I'm RxBuddy 👋 Ask me anything about ${drugName} — side effects, dosage, interactions, or warnings.` },
    ]
  );
  const [input,    setInput]    = useState("");
  const [isTyping, setIsTyping] = useState(false);

  const messagesEndRef = useRef(null);
  const inputRef       = useRef(null);

  // Persist messages across open/close
  useEffect(() => { _msgStore[storeKey] = messages; }, [messages, storeKey]);

  // After shell finishes expanding, fade content in
  useEffect(() => {
    if (isOpen && !isClosing) {
      const t = setTimeout(() => setContentVisible(true), 300);
      return () => clearTimeout(t);
    }
  }, [isOpen, isClosing]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  // Auto-focus
  useEffect(() => {
    if (contentVisible) {
      const t = setTimeout(() => inputRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [contentVisible]);

  // Open
  const handleOpen = () => {
    if (isOpen) return;
    setIsClosing(false);
    setIsOpen(true);
  };

  // Close: content fades out first, THEN shell collapses
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setContentVisible(false);           // content starts fading immediately
    setTimeout(() => {
      setIsOpen(false);                 // shell collapses after content is gone
      setIsClosing(false);
    }, 220);
  }, []);

  // Send message
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
        body: JSON.stringify({
          drug_name:            drugName,
          message:              text,
          conversation_history: history,
        }),
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

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600&display=swap');

        /* ── Root anchor ─────────────────────────────────── */
        .rxb-root {
          position: fixed;
          bottom: 28px;
          right: 28px;
          z-index: 9999;
          font-family: 'DM Sans', system-ui, sans-serif;
          /* anchor point is always the bottom-right corner */
          display: flex;
          align-items: flex-end;
          justify-content: flex-end;
        }

        /* ── Morphing shell ──────────────────────────────── */
        /*
          This single element is both the button and the chat panel.
          CSS transitions morph it between the two states.
          transform-origin: bottom right keeps it pinned to the corner.
        */
        .rxb-shell {
          position: relative;
          width: 120px;
          height: 120px;
          border-radius: 50%;
          background: transparent;
          box-shadow: none;
          cursor: pointer;
          overflow: hidden;
          transform-origin: bottom right;
          /* Spring-like easing — feels connected, not mechanical */
          transition:
            width        460ms cubic-bezier(0.34, 1.15, 0.64, 1),
            height       460ms cubic-bezier(0.34, 1.15, 0.64, 1),
            border-radius 460ms cubic-bezier(0.34, 1.15, 0.64, 1),
            box-shadow   300ms ease;
          will-change: width, height, border-radius;
          /* Entrance animation on first render */
          animation: rxb-bounceIn 0.55s cubic-bezier(0.34, 1.4, 0.64, 1) both;
        }

        @keyframes rxb-bounceIn {
          0%   { opacity: 0; transform: scale(0.4); }
          60%  { opacity: 1; transform: scale(1.06); }
          100% { opacity: 1; transform: scale(1); }
        }

        .rxb-shell.open {
          width: 360px;
          height: 520px;
          border-radius: 20px;
          box-shadow:
            0 24px 64px rgba(0,0,0,0.22),
            0  4px 16px rgba(0,0,0,0.12);
          cursor: default;
        }

        @media (max-width: 480px) {
          .rxb-root { bottom: 16px; right: 16px; }
          .rxb-shell.open {
            width: calc(100vw - 32px);
            height: 480px;
          }
        }

        /* ── Robot button ────────────────────────────────── */
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
          transform: scale(0.6);
        }

        .rxb-btn img {
          /* Fill the shell 100%, then zoom past the white padding ring
             so the robot's own black border aligns with the button edge.
             overflow:hidden on .rxb-shell clips the excess. */
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
          transform: scale(1.68);
          transition: transform 180ms ease;
          pointer-events: none;
          user-select: none;
          -webkit-user-drag: none;
        }

        /* Hover: nudge scale up from the new baseline */
        .rxb-shell:not(.open):hover .rxb-btn img {
          transform: scale(1.76);
        }

        /* ── Chat panel ──────────────────────────────────── */
        .rxb-panel {
          position: absolute;
          inset: 0;
          display: flex;
          flex-direction: column;
          opacity: 0;
          pointer-events: none;
          /* Content fades in AFTER shell expands */
          transition: opacity 180ms ease;
        }

        .rxb-panel.visible {
          opacity: 1;
          pointer-events: all;
        }

        /* ── Header ──────────────────────────────────────── */
        .rxb-header {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 14px 16px;
          background: #111827;
          border-radius: 20px 20px 0 0;
          flex-shrink: 0;
          /* Stagger in after panel fades */
          transform: translateY(-6px);
          opacity: 0;
          transition: transform 240ms ease, opacity 240ms ease;
          transition-delay: 0ms;
        }

        .rxb-panel.visible .rxb-header {
          transform: translateY(0);
          opacity: 1;
          transition-delay: 40ms;
        }

        .rxb-header-dot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: #22c55e;
          flex-shrink: 0;
          box-shadow: 0 0 6px rgba(34,197,94,0.6);
        }

        .rxb-header-text { flex: 1; min-width: 0; }

        .rxb-header-title {
          font-size: 14px;
          font-weight: 600;
          color: #fff;
          line-height: 1.2;
        }

        .rxb-header-sub {
          font-size: 11px;
          color: #9ca3af;
          line-height: 1.2;
          margin-top: 2px;
          text-transform: capitalize;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .rxb-close {
          background: none;
          border: none;
          color: #9ca3af;
          cursor: pointer;
          padding: 4px 6px;
          border-radius: 6px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: color 140ms ease, background 140ms ease;
          font-size: 18px;
          line-height: 1;
          flex-shrink: 0;
        }
        .rxb-close:hover { color: #fff; background: rgba(255,255,255,0.08); }

        /* ── Messages ────────────────────────────────────── */
        .rxb-messages {
          flex: 1;
          overflow-y: auto;
          padding: 14px 14px 8px;
          background: #111827;
          display: flex;
          flex-direction: column;
          gap: 10px;
          /* Stagger */
          transform: translateY(8px);
          opacity: 0;
          transition: transform 260ms ease, opacity 260ms ease;
          transition-delay: 0ms;
        }

        .rxb-panel.visible .rxb-messages {
          transform: translateY(0);
          opacity: 1;
          transition-delay: 80ms;
        }

        .rxb-messages::-webkit-scrollbar       { width: 4px; }
        .rxb-messages::-webkit-scrollbar-track { background: transparent; }
        .rxb-messages::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }

        /* ── Message bubbles ─────────────────────────────── */
        .rxb-bubble {
          max-width: 85%;
          padding: 10px 13px;
          border-radius: 14px;
          font-size: 13px;
          line-height: 1.55;
          word-break: break-word;
          animation: rxb-bubbleIn 200ms ease forwards;
        }

        @keyframes rxb-bubbleIn {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }

        .rxb-bubble.assistant {
          background: #1f2937;
          color: #e5e7eb;
          border-bottom-left-radius: 4px;
          align-self: flex-start;
        }

        .rxb-bubble.user {
          background: #059669;
          color: #fff;
          border-bottom-right-radius: 4px;
          align-self: flex-end;
        }

        /* ── Typing indicator ────────────────────────────── */
        .rxb-typing {
          display: flex;
          align-items: center;
          gap: 4px;
          padding: 10px 14px;
          background: #1f2937;
          border-radius: 14px;
          border-bottom-left-radius: 4px;
          align-self: flex-start;
          width: 52px;
        }

        .rxb-typing span {
          width: 6px;
          height: 6px;
          background: #6b7280;
          border-radius: 50%;
          animation: rxb-typingDot 1.2s ease infinite;
        }
        .rxb-typing span:nth-child(2) { animation-delay: 0.18s; }
        .rxb-typing span:nth-child(3) { animation-delay: 0.36s; }

        @keyframes rxb-typingDot {
          0%, 60%, 100% { transform: translateY(0);    opacity: 0.4; }
          30%            { transform: translateY(-4px); opacity: 1;   }
        }

        /* ── Input area ──────────────────────────────────── */
        .rxb-input-area {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px 14px;
          background: #111827;
          border-top: 1px solid #1f2937;
          border-radius: 0 0 20px 20px;
          flex-shrink: 0;
          /* Stagger */
          transform: translateY(8px);
          opacity: 0;
          transition: transform 260ms ease, opacity 260ms ease;
          transition-delay: 0ms;
        }

        .rxb-panel.visible .rxb-input-area {
          transform: translateY(0);
          opacity: 1;
          transition-delay: 120ms;
        }

        .rxb-input {
          flex: 1;
          min-width: 0;
          background: #1f2937;
          border: 1px solid #374151;
          border-radius: 10px;
          padding: 9px 13px;
          font-size: 13px;
          color: #e5e7eb;
          font-family: 'DM Sans', system-ui, sans-serif;
          outline: none;
          transition: border-color 150ms ease;
        }
        .rxb-input::placeholder { color: #6b7280; }
        .rxb-input:focus         { border-color: #059669; }

        .rxb-send {
          width: 36px;
          height: 36px;
          border-radius: 10px;
          background: #059669;
          border: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          transition: background 140ms ease, transform 100ms ease;
        }
        .rxb-send:hover:not(:disabled) { background: #047857; }
        .rxb-send:active:not(:disabled) { transform: scale(0.91); }
        .rxb-send:disabled { background: #374151; cursor: not-allowed; }

        .rxb-send svg { width: 15px; height: 15px; fill: #fff; }
      `}</style>

      <div className="rxb-root">
        <div
          className={`rxb-shell${isOpen ? " open" : ""}`}
          onClick={!isOpen ? handleOpen : undefined}
          role={isOpen ? "dialog" : "button"}
          aria-label={isOpen ? "RxBuddy chat" : "Open RxBuddy chat"}
          aria-expanded={isOpen}
        >
          {/* ── Robot button (visible when closed) ── */}
          <div className={`rxb-btn${isOpen ? " hidden" : ""}`}>
            <img src={ROBOT_IMAGE} alt="Ask RxBuddy" />
          </div>

          {/* ── Chat panel (visible when open) ── */}
          <div className={`rxb-panel${contentVisible ? " visible" : ""}`}>

            {/* Header */}
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

            {/* Messages */}
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

            {/* Input */}
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
    </>
  );
}
