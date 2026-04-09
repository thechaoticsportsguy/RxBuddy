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

/* ── Robot pharmacist image icon ──────────────────────────── */
function RobotIcon() {
  return (
    <img
      src="/rxbuddy-robot1.png"
      alt=""
      aria-hidden="true"
      style={{ width: "200px", height: "200px", display: "block" }}
    />
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

      {/* ── Trigger button ───────────────────────────────── */}
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
