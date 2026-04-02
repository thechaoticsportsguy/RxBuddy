/**
 * DrugChatWidget — Floating AI assistant chat for drug-related results.
 *
 * Bottom-right chat bubble that expands into a glass-morphism drawer.
 * Sends messages to POST /v2/chat scoped to a specific drug name.
 *
 * Props:
 *   drugName  — the drug being discussed
 *   isVisible — whether to render at all
 */

import { useState, useRef, useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export default function DrugChatWidget({ drugName, isVisible }) {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // Seed opening message when first opened
  useEffect(() => {
    if (isOpen && messages.length === 0) {
      setMessages([{
        role: "assistant",
        content: `Hi! I'm your RxBuddy AI assistant \u{1F44B} I can answer questions about ${drugName || "this medication"}. Ask me about side effects, interactions, dosage, warnings, or anything else!`,
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
      // Build conversation_history (exclude the opening assistant greeting)
      const history = messages
        .filter((_, i) => i > 0) // skip greeting
        .slice(-6); // last 3 turns

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

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply || "Sorry, I couldn't generate a response." },
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
      {/* ── Floating chat bubble ──────────────────────────────────── */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 100,
            width: 56,
            height: 56,
            borderRadius: "50%",
            background: "#0f172a",
            border: "1px solid rgba(255,255,255,0.15)",
            color: "#fff",
            fontSize: 24,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 4px 20px rgba(0,0,0,0.4), 0 0 20px rgba(56,100,220,0.3)",
            transition: "transform 0.2s ease, box-shadow 0.2s ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = "scale(1.1)";
            e.currentTarget.style.boxShadow = "0 4px 24px rgba(0,0,0,0.5), 0 0 30px rgba(56,100,220,0.5)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "scale(1)";
            e.currentTarget.style.boxShadow = "0 4px 20px rgba(0,0,0,0.4), 0 0 20px rgba(56,100,220,0.3)";
          }}
          aria-label="Open RxBuddy AI chat"
        >
          {"\uD83D\uDC8A"}
        </button>
      )}

      {/* ── Chat drawer ───────────────────────────────────────────── */}
      {isOpen && (
        <div style={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 100,
          width: 380,
          height: 500,
          background: "rgba(10,15,30,0.95)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 16,
          boxShadow: "0 25px 50px rgba(0,0,0,0.5)",
          display: "flex",
          flexDirection: "column",
          fontFamily: "'Inter', system-ui, sans-serif",
          animation: "slideUp 0.25s ease-out",
        }}>
          {/* Header */}
          <div style={{
            padding: "16px 20px 12px",
            borderBottom: "1px solid rgba(255,255,255,0.08)",
            flexShrink: 0,
          }}>
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}>
              <span style={{
                fontSize: 16,
                fontWeight: 700,
                color: "#fff",
              }}>
                {"\uD83D\uDC8A RxBuddy Assistant"}
              </span>
              <button
                onClick={() => setIsOpen(false)}
                style={{
                  background: "rgba(255,255,255,0.08)",
                  border: "none",
                  color: "rgba(255,255,255,0.6)",
                  fontSize: 18,
                  width: 28,
                  height: 28,
                  borderRadius: 8,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.15)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.08)"; }}
                aria-label="Close chat"
              >
                {"\u00D7"}
              </button>
            </div>
            <div style={{
              fontSize: 12,
              color: "rgba(160,180,220,0.6)",
              marginTop: 4,
            }}>
              {"Ask me anything about " + (drugName || "this medication")}
            </div>
          </div>

          {/* Messages area */}
          <div style={{
            flex: 1,
            overflowY: "auto",
            padding: "12px 16px",
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
                  padding: "10px 14px",
                  borderRadius: msg.role === "user" ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                  background: msg.role === "user"
                    ? "rgba(56,100,220,0.5)"
                    : "rgba(255,255,255,0.07)",
                  color: "#fff",
                  fontSize: 13,
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                }}>
                  {msg.content}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {isLoading && (
              <div style={{ alignSelf: "flex-start", maxWidth: "85%" }}>
                <div style={{
                  padding: "10px 14px",
                  borderRadius: "14px 14px 14px 4px",
                  background: "rgba(255,255,255,0.07)",
                  color: "rgba(160,180,220,0.7)",
                  fontSize: 13,
                  display: "flex",
                  gap: 4,
                }}>
                  <span style={{ animation: "dotPulse 1.2s infinite", animationDelay: "0s" }}>{"\u2022"}</span>
                  <span style={{ animation: "dotPulse 1.2s infinite", animationDelay: "0.2s" }}>{"\u2022"}</span>
                  <span style={{ animation: "dotPulse 1.2s infinite", animationDelay: "0.4s" }}>{"\u2022"}</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input area */}
          <div style={{
            padding: "12px 16px",
            borderTop: "1px solid rgba(255,255,255,0.08)",
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
              placeholder={"Ask about " + (drugName || "this drug") + "..."}
              style={{
                flex: 1,
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 10,
                padding: "10px 14px",
                color: "#fff",
                fontSize: 13,
                outline: "none",
                fontFamily: "inherit",
              }}
            />
            <button
              onClick={sendMessage}
              disabled={isLoading || !inputText.trim()}
              style={{
                background: isLoading || !inputText.trim()
                  ? "rgba(56,100,220,0.25)"
                  : "rgba(56,100,220,0.6)",
                border: "none",
                borderRadius: 10,
                padding: "10px 16px",
                color: "#fff",
                fontSize: 13,
                fontWeight: 600,
                cursor: isLoading || !inputText.trim() ? "default" : "pointer",
                transition: "background 0.15s",
                flexShrink: 0,
              }}
            >
              Send
            </button>
          </div>

          {/* Inline keyframes for animations */}
          <style jsx>{`
            @keyframes slideUp {
              from { opacity: 0; transform: translateY(20px); }
              to   { opacity: 1; transform: translateY(0); }
            }
            @keyframes dotPulse {
              0%, 80%, 100% { opacity: 0.3; }
              40% { opacity: 1; }
            }
          `}</style>
        </div>
      )}
    </>
  );
}
