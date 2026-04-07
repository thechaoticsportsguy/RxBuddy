/**
 * RxBuddyRobot — Floating pill badge button that opens DrugChatWidget.
 *
 * Shows a custom pill icon badge with a gentle floating animation.
 * On click, opens the chat drawer. When chat is open the badge hides;
 * when chat closes it reappears.
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

  return (
    <>
      {/* ── Robot button (hidden when chat is open) ───────────── */}
      <AnimatePresence>
        {!isChatOpen && (
          <motion.div
            key="robot"
            id="rx-pill-wrapper"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
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
            {/* Speech bubble — always visible */}
            <motion.div
              animate={{ scale: [1, 1.03, 1] }}
              transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
              style={{
                position: "absolute",
                bottom: "100%",
                marginBottom: 12,
                background: "#fff",
                border: "2px solid #222",
                borderRadius: 12,
                padding: "8px 14px",
                whiteSpace: "nowrap",
                pointerEvents: "none",
              }}
            >
              <span style={{
                fontFamily: FONT,
                fontSize: 13,
                color: "#222",
                fontWeight: 500,
              }}>
                {"Ask me more about \u201C" + (drugName || "your medication") + "\u201D"}
              </span>
              {/* Triangle pointer */}
              <div style={{
                position: "absolute",
                bottom: -8,
                left: "50%",
                transform: "translateX(-50%)",
                width: 0,
                height: 0,
                borderLeft: "8px solid transparent",
                borderRight: "8px solid transparent",
                borderTop: "8px solid #222",
              }} />
              <div style={{
                position: "absolute",
                bottom: -6,
                left: "50%",
                transform: "translateX(-50%)",
                width: 0,
                height: 0,
                borderLeft: "7px solid transparent",
                borderRight: "7px solid transparent",
                borderTop: "7px solid #fff",
              }} />
            </motion.div>

            {/* Pill badge icon */}
            <motion.div
              animate={{
                y: [0, -6, 0],
              }}
              transition={{
                y: { duration: 2, repeat: Infinity, ease: "easeInOut" },
              }}
              style={{
                width: 88,
                height: 88,
                borderRadius: "50%",
                background: "#0a0f1e",
                border: "2px solid rgba(255,255,255,0.15)",
                boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                userSelect: "none",
              }}
            >
              <div
                style={{
                  width: 56,
                  height: 22,
                  borderRadius: 11,
                  overflow: "hidden",
                  display: "flex",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.5)",
                  border: "1px solid rgba(255,255,255,0.2)",
                }}
              >
                <div style={{ width: "50%", height: "100%", background: "#111111" }} />
                <div
                  style={{
                    width: "50%",
                    height: "100%",
                    background: "#ffffff",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <span
                    style={{
                      fontSize: 7,
                      fontWeight: 800,
                      color: "#000",
                      fontFamily: "Inter, system-ui, sans-serif",
                      letterSpacing: "-0.3px",
                      lineHeight: 1,
                    }}
                  >
                    Rx
                  </span>
                </div>
              </div>
            </motion.div>
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
    </>
  );
}
