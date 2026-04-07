/**
 * RxBuddyRobot — Animated blob button that opens DrugChatWidget.
 *
 * A pulsing, morphing organic blob with a red/white gradient,
 * a two-tone pill capsule icon, and "RxBuddy" label.
 * Hover shows a speech bubble. Click opens the chat drawer.
 * Hidden while chat is open.
 *
 * Props:
 *   drugName — passed through to DrugChatWidget
 */

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import dynamic from "next/dynamic";

const DrugChatWidget = dynamic(() => import("./DrugChatWidget"), { ssr: false });

const FONT = "'Inter', system-ui, sans-serif";

export default function RxBuddyRobot({ drugName }) {
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  return (
    <>
      {/* ── Blob button (hidden when chat is open) ──────────── */}
      <AnimatePresence>
        {!isChatOpen && (
          <motion.div
            key="blob"
            id="rx-pill-wrapper"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
            onClick={() => setIsChatOpen(true)}
            style={{
              position: "fixed",
              bottom: 24,
              right: 24,
              zIndex: 999,
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            {/* ── Speech bubble (hover only) ──────────────────── */}
            <AnimatePresence>
              {isHovered && (
                <motion.div
                  key="tooltip"
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 4 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  style={{
                    position: "absolute",
                    bottom: "100%",
                    marginBottom: 10,
                    background: "#ffffff",
                    borderRadius: 8,
                    padding: "6px 10px",
                    whiteSpace: "nowrap",
                    pointerEvents: "none",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
                  }}
                >
                  <span
                    style={{
                      fontFamily: FONT,
                      fontSize: 12,
                      color: "#222",
                      fontWeight: 500,
                    }}
                  >
                    {"Ask me about \u201C" + (drugName || "your medication") + "\u201D"}
                  </span>
                  {/* Arrow */}
                  <div
                    style={{
                      position: "absolute",
                      bottom: -6,
                      left: "50%",
                      transform: "translateX(-50%)",
                      width: 0,
                      height: 0,
                      borderLeft: "6px solid transparent",
                      borderRight: "6px solid transparent",
                      borderTop: "6px solid #ffffff",
                    }}
                  />
                </motion.div>
              )}
            </AnimatePresence>

            {/* ── Blob ────────────────────────────────────────────── */}
            <div className="rx-blob">
              {/* Pill capsule icon */}
              <div
                style={{
                  width: 42,
                  height: 18,
                  borderRadius: 9,
                  overflow: "hidden",
                  display: "flex",
                  border: "1px solid rgba(0,0,0,0.2)",
                }}
              >
                <div
                  style={{
                    width: "50%",
                    height: "100%",
                    background: "#111",
                  }}
                />
                <div
                  style={{
                    width: "50%",
                    height: "100%",
                    background: "#fff",
                  }}
                />
              </div>

              {/* Label */}
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#fff",
                  lineHeight: 1,
                  userSelect: "none",
                }}
              >
                RxBuddy
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Chat drawer ───────────────────────────────────────── */}
      {isChatOpen && (
        <DrugChatWidget
          drugName={drugName}
          isVisible
          onClose={() => setIsChatOpen(false)}
        />
      )}

      {/* ── Keyframes ─────────────────────────────────────────── */}
      <style>{`
        @keyframes blobMorph {
          0%, 100% { border-radius: 60% 40% 30% 70% / 60% 30% 70% 40%; }
          25%      { border-radius: 30% 60% 70% 40% / 50% 60% 30% 60%; }
          50%      { border-radius: 50% 60% 30% 40% / 40% 60% 70% 50%; }
          75%      { border-radius: 40% 50% 60% 30% / 60% 40% 50% 70%; }
        }
        @keyframes blobPulse {
          0%, 100% { transform: scale(1); }
          50%      { transform: scale(1.06); }
        }
        .rx-blob {
          width: 90px;
          height: 90px;
          background: linear-gradient(135deg, #e11d48 0%, #ffffff 50%, #e11d48 100%);
          background-size: 200% 200%;
          animation: blobMorph 6s ease-in-out infinite, blobPulse 3s ease-in-out infinite;
          box-shadow: 0 8px 32px rgba(225, 29, 72, 0.4);
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 4px;
          cursor: pointer;
          user-select: none;
        }
      `}</style>
    </>
  );
}
