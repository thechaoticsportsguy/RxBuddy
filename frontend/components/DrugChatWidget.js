import { useState, useRef, useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// Persistent conversation memory (survives open/close cycles)
const _messageStore = {};

export default function DrugChatWidget({ drugName, isVisible, onClose }) {
  const storeKey = drugName || "__default__";
  const [messages, setMessages] = useState(() => _messageStore[storeKey] || []);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const drawerRef = useRef(null);

  // Sync messages to persistent store on every change
  useEffect(() => {
    _messageStore[storeKey] = messages;
  }, [messages, storeKey]);

  // Mobile detection
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 480);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  // Seed opening message (only if no stored messages)
  useEffect(() => {
    if (isVisible && messages.length === 0) {
      setMessages([
        {
          role: "assistant",
          content: `Hi! I'm RxBuddy 👋 Ask me anything about ${drugName || "your medication"} — side effects, dosage, interactions, or warnings.`,
        },
      ]);
    }
  }, [isVisible, drugName, messages.length]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Auto-focus input
  useEffect(() => {
    if (isVisible) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [isVisible]);

  // Click-outside detection
  useEffect(() => {
    if (!isVisible) return;

    function handleClickOutside(e) {
      const drawer = drawerRef.current;
      if (!drawer) return;

      if (drawer.contains(e.target)) return;

      const pillWrapper = document.getElementById("rx-pill-wrapper");
      if (pillWrapper && pillWrapper.contains(e.target)) return;

      if (onClose) onClose();
    }

    const timer = setTimeout(() => {
      document.addEventListener("mousedown", handleClickOutside);
    }, 100);

    return () => {
      clearTimeout(timer);
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isVisible, onClose]);

  const sendMessage = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isLoading) return;

    const userMsg = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInputText("");
    setIsLoading(true);

    try {
      const history = messages.filter((_, i) => i > 0).slice(-6);
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
        {
          role: "assistant",
          content: data.reply || "Sorry, I couldn't generate a response.",
        },
      ]);
    } catch (err) {
      console.error("[DrugChatWidget] Error:", err);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "I'm having trouble connecting right now. Please try again in a moment.",
        },
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
    <div
      id="rx-chat-drawer"
      ref={drawerRef}
      style={{
        position: "fixed",
        bottom: isMobile ? 0 : 90,
        right: isMobile ? 0 : 24,
        width: isMobile ? "100vw" : 400,
        zIndex: 1000,
        overflow: "visible",
        pointerEvents: "auto",
      }}
    >
      {/* ── Chat shell ──────────────────────────────────────────── */}
      <div
        style={{
          background: "#0a0f1e",
          borderRadius: isMobile ? 0 : 16,
          border: "1px solid rgba(255,255,255,0.08)",
          boxShadow: "0 25px 50px rgba(0,0,0,0.5)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          height: isMobile ? "100vh" : 520,
        }}
      >
        {/* ── Header ──────────────────────────────────────────────── */}
        <div
          style={{
            background: "rgba(255,255,255,0.03)",
            borderBottom: "1px solid rgba(255,255,255,0.07)",
            padding: "16px 20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <div>
            <div
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: "#22c55e",
                }}
              />
              <span
                style={{
                  color: "white",
                  fontWeight: 700,
                  fontSize: 15,
                  fontFamily: "Inter,system-ui,sans-serif",
                }}
              >
                RxBuddy Assistant
              </span>
            </div>
            <div
              style={{
                color: "rgba(255,255,255,0.4)",
                fontSize: 12,
                fontFamily: "Inter,system-ui,sans-serif",
                marginTop: 2,
              }}
            >
              Ask me about {drugName}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "white",
              fontSize: 24,
              cursor: "pointer",
              padding: 0,
              lineHeight: 1,
            }}
            aria-label="Close chat"
          >
            {"\u00D7"}
          </button>
        </div>

        {/* ── Messages ────────────────────────────────────────────── */}
        <div
          className="rx-messages"
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "16px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
            scrollbarWidth: "thin",
            scrollbarColor: "rgba(255,255,255,0.1) transparent",
          }}
        >
          {messages.map((msg, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent:
                  msg.role === "user" ? "flex-end" : "flex-start",
              }}
            >
              <div
                style={{
                  background:
                    msg.role === "user" ? "#4a9eff" : "#1a2035",
                  color: "white",
                  borderRadius:
                    msg.role === "user"
                      ? "16px 16px 4px 16px"
                      : "16px 16px 16px 4px",
                  padding: "10px 14px",
                  maxWidth: "80%",
                  fontSize: 14,
                  fontFamily: "Inter,system-ui,sans-serif",
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                }}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {/* Typing indicator */}
          {isLoading && (
            <div style={{ display: "flex", justifyContent: "flex-start" }}>
              <div
                style={{
                  background: "#1a2035",
                  color: "white",
                  borderRadius: "16px 16px 16px 4px",
                  padding: "10px 14px",
                  maxWidth: "80%",
                  display: "flex",
                  gap: 5,
                  alignItems: "center",
                  height: 38,
                }}
              >
                <span
                  className="rx-dot"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "#4a9eff",
                    display: "inline-block",
                  }}
                />
                <span
                  className="rx-dot"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "#4a9eff",
                    display: "inline-block",
                  }}
                />
                <span
                  className="rx-dot"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "#4a9eff",
                    display: "inline-block",
                  }}
                />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* ── Input area ──────────────────────────────────────────── */}
        <div
          style={{
            borderTop: "1px solid rgba(255,255,255,0.07)",
            padding: "12px 16px",
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Ask about ${drugName}...`}
            style={{
              flex: 1,
              background: "#111827",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 24,
              padding: "10px 16px",
              color: "white",
              fontSize: 14,
              fontFamily: "Inter,system-ui,sans-serif",
              outline: "none",
            }}
          />
          <button
            onClick={sendMessage}
            disabled={isLoading || !inputText.trim()}
            className="rx-send-btn"
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background:
                isLoading || !inputText.trim()
                  ? "rgba(74,158,255,0.3)"
                  : "#4a9eff",
              border: "none",
              cursor:
                isLoading || !inputText.trim() ? "default" : "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "white",
              fontSize: 18,
              flexShrink: 0,
              transition: "background 0.15s",
            }}
          >
            {"\u2192"}
          </button>
        </div>
      </div>

      {/* ── CSS keyframes ─────────────────────────────────────────── */}
      <style>{`
        @keyframes dotPulse {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40%           { transform: scale(1);   opacity: 1;   }
        }
        .rx-dot {
          animation: dotPulse 1.4s ease-in-out infinite;
        }
        .rx-dot:nth-child(2) { animation-delay: 0.2s; }
        .rx-dot:nth-child(3) { animation-delay: 0.4s; }
        .rx-messages::-webkit-scrollbar { width: 6px; }
        .rx-messages::-webkit-scrollbar-track { background: transparent; }
        .rx-messages::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.1);
          border-radius: 3px;
        }
        .rx-send-btn:hover:not(:disabled) {
          background: #3a8eef !important;
        }
      `}</style>
    </div>
  );
}
