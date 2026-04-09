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

/* ── Inline SVG robot pharmacist icon ─────────────────────── */
function RobotIcon({ hovered }) {
  return (
    <svg
      width="42"
      height="44"
      viewBox="0 0 44 46"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      {/* ── Antenna ── */}
      <circle cx="22" cy="2.5" r="2.2" fill="white" />
      <line
        x1="22" y1="4.6"
        x2="22" y2="9"
        stroke="white" strokeWidth="1.8" strokeLinecap="round"
      />

      {/* ── Head ── */}
      <rect
        x="8" y="9" width="28" height="16" rx="7"
        fill="white" fillOpacity="0.95"
      />

      {/* ── Earpieces / headset ── */}
      <rect x="4"  y="12" width="5" height="8" rx="2.5" fill="white" fillOpacity="0.8" />
      <rect x="35" y="12" width="5" height="8" rx="2.5" fill="white" fillOpacity="0.8" />

      {/* ── Left eye ── */}
      <circle
        className={styles.robotEye}
        cx="16" cy="16" r="3.2"
        fill="#1dd1a1"
      />
      {/* left eye shine */}
      <circle cx="17.1" cy="14.7" r="1.1" fill="white" fillOpacity="0.75" />

      {/* ── Right eye ── */}
      <circle
        className={styles.robotEye}
        cx="28" cy="16" r="3.2"
        fill="#1dd1a1"
      />
      {/* right eye shine */}
      <circle cx="29.1" cy="14.7" r="1.1" fill="white" fillOpacity="0.75" />

      {/* ── Smile ── */}
      <path
        d="M14 21 Q22 25.5 30 21"
        stroke="#1dd1a1" strokeWidth="1.6" strokeLinecap="round" fill="none"
      />

      {/* ── Body / lab coat ── */}
      <rect
        x="11" y="26" width="22" height="14" rx="3.5"
        fill="white" fillOpacity="0.9"
      />

      {/* Collar V-lines */}
      <path d="M22 26 L19 29 L22 28 L25 29 Z" fill="#d0d0d0" />

      {/* ── Medical cross badge (right breast) ── */}
      <rect x="26" y="28" width="5.5" height="5.5" rx="1.2" fill="#ff6b6b" />
      {/* horizontal bar */}
      <rect x="27.1" y="30" width="3.3" height="1.5" rx="0.4" fill="white" />
      {/* vertical bar */}
      <rect x="28.5" y="28.7" width="1.5" height="4.1" rx="0.4" fill="white" />

      {/* ── Left arm ── */}
      <rect x="6" y="26" width="6" height="11" rx="3" fill="white" fillOpacity="0.85" />

      {/* ── Right arm ── */}
      <rect x="32" y="26" width="6" height="11" rx="3" fill="white" fillOpacity="0.85" />

      {/* ── Pill bottle in right hand ── */}
      {/* cap */}
      <rect x="33.5" y="34" width="4.5" height="2.5" rx="0.8" fill="#00b894" />
      {/* bottle body */}
      <rect x="33" y="36" width="5.5" height="6" rx="1.5" fill="#1dd1a1" />
      {/* label stripe */}
      <rect x="33" y="38" width="5.5" height="1.5" rx="0.5" fill="white" fillOpacity="0.5" />

      {/* ── Feet ── */}
      <rect x="13" y="39" width="7" height="4" rx="2" fill="white" fillOpacity="0.8" />
      <rect x="24" y="39" width="7" height="4" rx="2" fill="white" fillOpacity="0.8" />
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
        <RobotIcon hovered={isHovered} />

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
