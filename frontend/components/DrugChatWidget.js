/**
 * DrugChatWidget — Premium dark-glass AI chat drawer.
 *
 * Click-outside closes the drawer but conversation persists in a ref.
 * Reopening restores previous messages. Parent controls visibility
 * via isVisible; onClose fires on close button or click-outside.
 *
 * Props:
 *   drugName  — the drug being discussed
 *   isVisible — whether to render the drawer
 *   onClose   — callback when close button is clicked or click-outside
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import RxBuddyRobot from "./ui/RxBuddyRobot";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

// ── Design tokens ──────────────────────────────────────────────
const FONT = "'Inter', system-ui, sans-serif";
const BG = "#0a0f1e";
const BG_LIGHT = "#111827";
const BG_USER = "#0c1a3a";
const BORDER = "rgba(255,255,255,0.08)";
const BORDER_GLOW = "rgba(74,158,255,0.15)";
const TEXT = "#e2e8f0";
const TEXT_MUTED = "#94a3b8";
const ACCENT = "#4a9eff";
const INPUT_BG = "#0f172a";
const CHAT_DRAWER_ID = "rx-chat-drawer";

// Persistent conversation memory (survives open/close cycles)
const _messageStore = {};

export default function DrugChatWidget({ drugName, isVisible, onClose }) {
  const storeKey = drugName || "__default__";
  const [messages, setMessages] = useState(() => _messageStore[storeKey] || []);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [inputFocused, setInputFocused] = useState(false);
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
      setMessages([{
        role: "assistant",
        content: `Hi! I'm RxBuddy, your AI medication assistant. Ask me anything about ${drugName || "your medication"} \u2014 side effects, interactions, dosage, or warnings.`,
      }]);
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

      // If click is inside the drawer, ignore
      if (drawer.contains(e.target)) return;

      // If click is on the pill wrapper, ignore (pill handles its own click)
      const pillWrapper = document.getElementById("rx-pill-wrapper");
      if (pillWrapper && pillWrapper.contains(e.target)) return;

      // Click is outside — close
      if (onClose) onClose();
    }

    // Small delay so the opening click doesn't immediately close
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

  const drawerWidth = isMobile ? "100vw" : 400;
  const drawerHeight = isMobile ? "calc(100vh - 60px)" : 560;
  const drawerBottom = isMobile ? 0 : 24;
  const drawerRight = isMobile ? 0 : 24;
  const drawerRadius = isMobile ? "16px 16px 0 0" : 16;

  return (
    <motion.div
      ref={drawerRef}
      id={CHAT_DRAWER_ID}
      initial={{ opacity: 0, y: 30, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 20, scale: 0.98 }}
      transition={{ type: "spring", damping: 25, stiffness: 300 }}
      style={{
        position: "fixed",
        bottom: drawerBottom,
        right: drawerRight,
        zIndex: 9999,
        width: drawerWidth,
        height: drawerHeight,
        background: BG,
        borderRadius: drawerRadius,
        border: `1px solid ${BORDER_GLOW}`,
        boxShadow: "0 0 40px rgba(74,158,255,0.08), 0 20px 60px rgba(0,0,0,0.5)",
        display: "flex",
        flexDirection: "column",
        fontFamily: FONT,
        overflow: "visible",
      }}
    >
      {/* ── Robot mascot (above drawer) ───────────────────────── */}
      {!isMobile && (
        <div style={{
          position: "absolute",
          top: -45,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 1,
          pointerEvents: "none",
        }}>
          <RxBuddyRobot size={70} glowColor={ACCENT} />
        </div>
      )}

      {/* ── Header ────────────────────────────────────────────── */}
      <div style={{
        padding: "22px 20px 14px",
        borderBottom: `1px solid ${BORDER}`,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <div>
          <div style={{
            fontSize: 16,
            fontWeight: 600,
            color: "#fff",
            letterSpacing: "-0.01em",
          }}>
            RxBuddy
          </div>
          <div style={{
            fontSize: 11,
            color: TEXT_MUTED,
            marginTop: 2,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}>
            <span style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "#22c55e",
              display: "inline-block",
              animation: "statusPulse 2s ease-in-out infinite",
            }} />
            AI Medication Assistant
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            background: "rgba(255,255,255,0.05)",
            border: "none",
            color: TEXT_MUTED,
            fontSize: 18,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "background 0.15s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.12)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.05)"; }}
          aria-label="Close chat"
        >
          {"\u00D7"}
        </button>
      </div>

      {/* ── Messages ──────────────────────────────────────────── */}
      <div className="rxchat-messages" style={{
        flex: 1,
        overflowY: "auto",
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}>
        {messages.map((msg, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            style={{
              alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
            }}
          >
            <div style={{
              padding: "10px 14px",
              borderRadius: msg.role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
              background: msg.role === "user" ? BG_USER : BG_LIGHT,
              border: msg.role === "user"
                ? "1px solid rgba(74,158,255,0.12)"
                : `1px solid ${BORDER}`,
              color: TEXT,
              fontSize: 13.5,
              lineHeight: 1.55,
              wordBreak: "break-word",
              fontFamily: FONT,
            }}>
              {msg.content}
            </div>
          </motion.div>
        ))}

        {/* Typing indicator */}
        {isLoading && (
          <div style={{ alignSelf: "flex-start", maxWidth: "85%" }}>
            <div style={{
              padding: "10px 14px",
              borderRadius: "16px 16px 16px 4px",
              background: BG_LIGHT,
              border: `1px solid ${BORDER}`,
              display: "flex",
              gap: 5,
              alignItems: "center",
            }}>
              <span className="rxchat-dot" style={{ animationDelay: "0s" }} />
              <span className="rxchat-dot" style={{ animationDelay: "0.15s" }} />
              <span className="rxchat-dot" style={{ animationDelay: "0.3s" }} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Input area ────────────────────────────────────────── */}
      <div style={{
        padding: "12px 16px",
        borderTop: `1px solid ${BORDER}`,
        display: "flex",
        gap: 8,
        flexShrink: 0,
        alignItems: "center",
      }}>
        <input
          ref={inputRef}
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setInputFocused(true)}
          onBlur={() => setInputFocused(false)}
          placeholder="Ask about medications..."
          style={{
            flex: 1,
            background: INPUT_BG,
            border: `1px solid ${inputFocused ? ACCENT : "rgba(255,255,255,0.08)"}`,
            borderRadius: 12,
            padding: "11px 14px",
            color: TEXT,
            fontSize: 13.5,
            outline: "none",
            fontFamily: FONT,
            transition: "border-color 0.2s, box-shadow 0.2s",
            boxShadow: inputFocused ? "0 0 0 2px rgba(74,158,255,0.15)" : "none",
          }}
        />
        <button
          onClick={sendMessage}
          disabled={isLoading || !inputText.trim()}
          style={{
            width: 40,
            height: 40,
            borderRadius: "50%",
            background: isLoading || !inputText.trim() ? "rgba(74,158,255,0.2)" : ACCENT,
            border: "none",
            cursor: isLoading || !inputText.trim() ? "default" : "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "background 0.15s",
            flexShrink: 0,
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="19" x2="12" y2="5" />
            <polyline points="5 12 12 5 19 12" />
          </svg>
        </button>
      </div>

      {/* ── Inline styles ─────────────────────────────────────── */}
      <style>{`
        .rxchat-messages::-webkit-scrollbar { width: 6px; }
        .rxchat-messages::-webkit-scrollbar-track { background: rgba(255,255,255,0.02); }
        .rxchat-messages::-webkit-scrollbar-thumb {
          background: rgba(74,158,255,0.25);
          border-radius: 3px;
        }
        .rxchat-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: ${ACCENT};
          animation: rxDotPulse 1.2s infinite;
        }
        @keyframes rxDotPulse {
          0%, 80%, 100% { opacity: 0.25; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
        @keyframes statusPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
    </motion.div>
  );
}
