/**
 * DrugChatWidget — Black & white brutalist AI chat for drug results.
 *
 * Sharp corners, Times New Roman, pure black/white palette.
 * Sends messages to POST /v2/chat scoped to a specific drug name.
 *
 * Props:
 *   drugName  — the drug being discussed
 *   isVisible — whether to render at all
 */

import { useState, useRef, useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

function PillLogo() {
  return (
    <svg width="40" height="28" viewBox="0 0 40 28" style={{ flexShrink: 0 }}>
      {/* Pill body */}
      <rect x="2" y="4" width="36" height="16" rx="8" fill="white" stroke="black" strokeWidth="1.5"/>
      {/* Divider line */}
      <line x1="20" y1="4" x2="20" y2="20" stroke="black" strokeWidth="1.5"/>
      {/* Rx text */}
      <text x="8" y="16" fontSize="9" fontFamily="Times New Roman, serif" fill="black" fontWeight="bold">Rx</text>
      {/* Robot eyes on right half */}
      <circle cx="26" cy="11" r="2" fill="black"/>
      <circle cx="33" cy="11" r="2" fill="black"/>
      {/* T-shirt shape below pill */}
      <path d="M15 20 L17 23 L23 23 L25 20" fill="white" stroke="black" strokeWidth="1"/>
      <text x="17" y="26" fontSize="5" fontFamily="Times New Roman, serif" fill="black">Rx</text>
    </svg>
  );
}

export default function DrugChatWidget({ drugName, isVisible }) {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  const FONT = "'Times New Roman', Times, serif";

  // Seed opening message when first opened
  useEffect(() => {
    if (isOpen && messages.length === 0) {
      setMessages([{
        role: "assistant",
        content: `Hello! I'm your RxBuddy AI assistant. I can answer questions about ${drugName || "this medication"} \u2014 side effects, interactions, dosage, warnings, and more.\nAny questions about your prescription? Ask me, I can help! \uD83D\uDC8A`,
      }]);
    }
  }, [isOpen, drugName, messages.length]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Focus input when drawer opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [isOpen]);

  const sendMessage = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isLoading) return;

    const userMsg = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInputText("");
    setIsLoading(true);

    try {
      const history = messages
        .filter((_, i) => i > 0)
        .slice(-6);

      const res = await fetch(`${API_BASE}/v2/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          drug_name: drugName,
          message: text,
          conversation_history: history,
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const reply = data.reply || "Sorry, I couldn't generate a response.";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply + "\n\nAny other questions on your prescription? Ask me, I can help! \uD83D\uDC8A" },
      ]);
    } catch (err) {
      console.error("[DrugChatWidget] Error:", err);
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

  if (!isVisible) return null;

  return (
    <>
      {/* ── Rectangular chat button ───────────────────────────────── */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 999,
            background: "#000000",
            color: "#ffffff",
            border: "2px solid #ffffff",
            borderRadius: 0,
            padding: "12px 20px",
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 700,
            cursor: "pointer",
            transition: "background 0.15s, color 0.15s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "#ffffff";
            e.currentTarget.style.color = "#000000";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "#000000";
            e.currentTarget.style.color = "#ffffff";
          }}
          aria-label="Open RxBuddy AI chat"
        >
          {"\uD83D\uDC8A Ask RxBuddy AI"}
        </button>
      )}

      {/* ── Chat drawer ───────────────────────────────────────────── */}
      {isOpen && (
        <div style={{
          position: "fixed",
          bottom: 70,
          right: 24,
          zIndex: 999,
          width: 380,
          height: 520,
          background: "#000000",
          border: "2px solid #ffffff",
          borderRadius: 0,
          display: "flex",
          flexDirection: "column",
          fontFamily: FONT,
          animation: "chatSlideUp 0.2s ease-out",
        }}>
          {/* Header */}
          <div style={{
            padding: "14px 16px",
            borderBottom: "1px solid #ffffff",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <PillLogo />
              <span style={{
                fontSize: 18,
                fontWeight: 700,
                color: "#ffffff",
                fontFamily: FONT,
              }}>
                RxBuddy Assistant
              </span>
            </div>
            <button
              onClick={() => setIsOpen(false)}
              style={{
                background: "transparent",
                border: "none",
                color: "#ffffff",
                fontSize: 24,
                cursor: "pointer",
                padding: "0 4px",
                fontFamily: FONT,
                lineHeight: 1,
              }}
              aria-label="Close chat"
            >
              {"\u00D7"}
            </button>
          </div>

          {/* Messages area */}
          <div style={{
            flex: 1,
            overflowY: "auto",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}>
            {messages.map((msg, i) => (
              <div
                key={i}
                style={{
                  alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
                  maxWidth: "85%",
                }}
              >
                <div style={{
                  padding: "8px 12px",
                  borderRadius: 0,
                  background: msg.role === "user" ? "#ffffff" : "#000000",
                  color: msg.role === "user" ? "#000000" : "#ffffff",
                  border: msg.role === "user" ? "1px solid #ffffff" : "1px solid #333333",
                  fontSize: 13,
                  lineHeight: 1.6,
                  wordBreak: "break-word",
                  fontFamily: FONT,
                  whiteSpace: "pre-wrap",
                }}>
                  {msg.content}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {isLoading && (
              <div style={{ alignSelf: "flex-start", maxWidth: "85%" }}>
                <div style={{
                  padding: "8px 12px",
                  borderRadius: 0,
                  background: "#000000",
                  border: "1px solid #ffffff",
                  color: "#ffffff",
                  fontSize: 16,
                  display: "flex",
                  gap: 6,
                }}>
                  <span style={{ animation: "chatDotPulse 1.2s infinite", animationDelay: "0s" }}>{"\u2022"}</span>
                  <span style={{ animation: "chatDotPulse 1.2s infinite", animationDelay: "0.2s" }}>{"\u2022"}</span>
                  <span style={{ animation: "chatDotPulse 1.2s infinite", animationDelay: "0.4s" }}>{"\u2022"}</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input area */}
          <div style={{
            padding: "12px 14px",
            borderTop: "1px solid #ffffff",
            display: "flex",
            gap: 8,
            flexShrink: 0,
          }}>
            <input
              ref={inputRef}
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your question..."
              style={{
                flex: 1,
                background: "#000000",
                border: "1px solid #ffffff",
                borderRadius: 0,
                padding: "10px 12px",
                color: "#ffffff",
                fontSize: 13,
                outline: "none",
                fontFamily: FONT,
              }}
            />
            <button
              onClick={sendMessage}
              disabled={isLoading || !inputText.trim()}
              style={{
                background: isLoading || !inputText.trim() ? "#333333" : "#ffffff",
                color: isLoading || !inputText.trim() ? "#666666" : "#000000",
                border: "1px solid #ffffff",
                borderRadius: 0,
                padding: "10px 16px",
                fontSize: 13,
                fontWeight: 700,
                fontFamily: FONT,
                cursor: isLoading || !inputText.trim() ? "default" : "pointer",
                transition: "background 0.15s, color 0.15s",
                flexShrink: 0,
              }}
            >
              Send
            </button>
          </div>

          {/* Inline keyframes */}
          <style jsx>{`
            @keyframes chatSlideUp {
              from { opacity: 0; transform: translateY(16px); }
              to   { opacity: 1; transform: translateY(0); }
            }
            @keyframes chatDotPulse {
              0%, 80%, 100% { opacity: 0.3; }
              40% { opacity: 1; }
            }
          `}</style>
        </div>
      )}
    </>
  );
}
