/**
 * ChatbotButton — animated circular chatbot trigger + chat modal.
 *
 * Drop-in replacement for RxBuddyRobot on any page. Self-contained:
 * no external dependencies beyond React + Next.js.
 *
 * Props:
 *   drugName {string} — passed to the chat API and welcome message
 */

import { useState, useRef, useEffect, useCallback } from "react";
import styles from "./ChatbotButton.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// Persistent conversation store — survives open/close cycles within the page
const _msgStore = {};

/* ── Inline SVG robot pharmacist icon (matches reference design) ─ */
function RobotIcon() {
  return (
    <svg
      width="48"
      height="52"
      viewBox="0 0 48 54"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* ── Antenna ── */}
      <circle cx="24" cy="2.5" r="2.8" fill="white" />
      <line x1="24" y1="5.2" x2="24" y2="10" stroke="white" strokeWidth="2.2" strokeLinecap="round" />

      {/* ── Head (large rounded rect) ── */}
      <rect x="8" y="10" width="32" height="21" rx="9" fill="white" fillOpacity="0.96" />

      {/* ── Left earpiece ── */}
      <circle cx="6"  cy="20.5" r="4.2" fill="white" fillOpacity="0.85" />
      {/* ── Right earpiece ── */}
      <circle cx="42" cy="20.5" r="4.2" fill="white" fillOpacity="0.85" />

      {/* ── Headset mic arm (right side) ── */}
      <path d="M44.5 23 Q47.5 27.5 44 30" stroke="white" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <circle cx="43.8" cy="30.5" r="2.2" fill="white" />

      {/* ── Visor eyes — wide dark bar with horizontal slats ── */}
      <rect x="12" y="14" width="24" height="9.5" rx="4" fill="rgba(5,18,12,0.92)" />
      {/* slat lines give the venetian-blind effect */}
      <line x1="13.2" y1="16.5" x2="34.8" y2="16.5" stroke="white" strokeOpacity="0.38" strokeWidth="1" />
      <line x1="13.2" y1="19"   x2="34.8" y2="19"   stroke="white" strokeOpacity="0.38" strokeWidth="1" />
      <line x1="13.2" y1="21.5" x2="34.8" y2="21.5" stroke="white" strokeOpacity="0.38" strokeWidth="1" />

      {/* ── Smile ── */}
      <path d="M17 27.5 Q24 32 31 27.5" stroke="white" strokeWidth="1.8" strokeLinecap="round" fill="none" />

      {/* ── Body / lab coat ── */}
      <rect x="12" y="32" width="24" height="22" rx="4.5" fill="white" fillOpacity="0.92" />

      {/* ── Collar V-lapels ── */}
      <path d="M24 32 L19.5 37 L24 35.5 L28.5 37 Z" fill="#d4d4d4" />

      {/* ── Coat button ── */}
      <circle cx="23.5" cy="41" r="1.5" fill="#c0c0c0" />

      {/* ── Medical cross badge (right breast) ── */}
      <rect x="27.5" y="34" width="7" height="7" rx="1.4" fill="#ff6b6b" />
      {/* horizontal */}
      <rect x="28.8" y="36.3" width="4.4" height="2.4" rx="0.5" fill="white" />
      {/* vertical */}
      <rect x="30.3" y="34.8" width="1.9" height="5.5" rx="0.5" fill="white" />

      {/* ── Left arm raised toward upper-left ── */}
      {/* thick rounded stroke = sleeve */}
      <line x1="13" y1="34" x2="4.5" y2="18" stroke="white" strokeWidth="8" strokeLinecap="round" strokeOpacity="0.9" />

      {/* ── Capsule / pill held in raised hand ── */}
      {/* body */}
      <rect x="1" y="7" width="7" height="14" rx="3.5" fill="white" fillOpacity="0.96" />
      {/* capsule mid-line (two-tone pharmaceutical look) */}
      <line x1="1" y1="14" x2="8" y2="14" stroke="#00b894" strokeWidth="1.4" strokeOpacity="0.75" />

      {/* ── Right arm (resting at side) ── */}
      <rect x="35" y="33" width="7.5" height="14" rx="3.8" fill="white" fillOpacity="0.88" />
    </svg>
  );
}

/* ── Main component ────────────────────────────────────────── */
export default function ChatbotButton({ drugName }) {
  const storeKey = drugName || "__default__";

  const [isOpen,    setIsOpen]    = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [messages,  setMessages]  = useState(() => _msgStore[storeKey] || []);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const messagesEndRef = useRef(null);
  const inputRef       = useRef(null);

  // Persist messages across open/close cycles
  useEffect(() => {
    _msgStore[storeKey] = messages;
  }, [messages, storeKey]);

  // Seed welcome message the first time the modal opens
  useEffect(() => {
    if (isOpen && messages.length === 0) {
      setMessages([
        {
          role: "assistant",
          content: `Hi! I'm RxBuddy 👋 Ask me anything about ${
            drugName || "your medication"
          } — side effects, dosage, interactions, or warnings.`,
        },
      ]);
    }
  }, [isOpen, drugName, messages.length]);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Auto-focus input when modal opens
  useEffect(() => {
    if (isOpen) {
      const t = setTimeout(() => inputRef.current?.focus(), 280);
      return () => clearTimeout(t);
    }
  }, [isOpen]);

  // Send message to the chat API
  const sendMessage = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isLoading) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInputText("");
    setIsLoading(true);

    try {
      // Send up to the last 6 exchanges (excluding the seed message)
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

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply || "Sorry, I couldn't generate a response." },
      ]);
    } catch (err) {
      console.error("[ChatbotButton] API error:", err);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "I'm having trouble connecting right now. Please try again in a moment." },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [inputText, isLoading, messages, drugName]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className={styles.container}>

      {/* Pulse ring — only visible when modal is closed */}
      {!isOpen && <div className={styles.pulseRing} aria-hidden="true" />}

      {/* ── Circular trigger button ──────────────────────── */}
      <button
        className={styles.button}
        onClick={() => setIsOpen((o) => !o)}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        aria-label={isOpen ? "Close RxBuddy chat" : "Open RxBuddy chat"}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
      >
        <RobotIcon />

        {/* Notification badge */}
        {!isOpen && (
          <span className={styles.badge} aria-label="Chat available">
            ?
          </span>
        )}
      </button>

      {/* ── Chat modal ───────────────────────────────────── */}
      {isOpen && (
        <div
          className={styles.modal}
          role="dialog"
          aria-label="RxBuddy chat assistant"
          aria-modal="true"
        >
          {/* Header */}
          <div className={styles.modalHeader}>
            <div>
              <div className={styles.modalTitleRow}>
                <div className={styles.onlineDot} aria-hidden="true" />
                <h2 className={styles.modalTitle}>Ask RxBuddy</h2>
              </div>
              <p className={styles.modalSubtitle}>
                About {drugName || "your medication"}
              </p>
            </div>

            <button
              className={styles.closeBtn}
              onClick={() => setIsOpen(false)}
              aria-label="Close chat"
            >
              ×
            </button>
          </div>

          {/* Messages */}
          <div
            className={styles.messages}
            role="log"
            aria-live="polite"
            aria-label="Chat messages"
          >
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`${styles.msgRow} ${
                  msg.role === "user"
                    ? styles.msgRowUser
                    : styles.msgRowAssistant
                }`}
              >
                <div
                  className={`${styles.msgBubble} ${
                    msg.role === "user"
                      ? styles.msgBubbleUser
                      : styles.msgBubbleAssistant
                  }`}
                >
                  {msg.content}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {isLoading && (
              <div
                className={styles.typingRow}
                aria-label="RxBuddy is typing"
              >
                <div
                  className={styles.typingDots}
                  aria-hidden="true"
                >
                  <span className={styles.typingDot} />
                  <span className={styles.typingDot} />
                  <span className={styles.typingDot} />
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input area */}
          <div className={styles.inputArea}>
            <input
              ref={inputRef}
              type="text"
              className={styles.input}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`Ask about ${drugName || "medication"}…`}
              aria-label="Type your question"
            />
            <button
              className={styles.sendBtn}
              onClick={sendMessage}
              disabled={isLoading || !inputText.trim()}
              aria-label="Send message"
            >
              →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
