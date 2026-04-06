/**
 * RxBuddyRobot — Floating robot image button that opens DrugChatWidget.
 *
 * Shows /rxbuddy-robot.png with a gentle floating animation. On hover,
 * scales up and shows a tooltip. On click, opens the chat drawer.
 * When chat is open the robot hides; when chat closes it reappears.
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

            {/* Robot image */}
            <motion.img
              src="/rxbuddy-robot.png"
              alt="RxBuddy Robot"
              draggable={false}
              animate={{
                y: [0, -6, 0],
              }}
              transition={{
                y: { duration: 2, repeat: Infinity, ease: "easeInOut" },
              }}
              style={{
                width: 100,
                height: "auto",
                userSelect: "none",
                filter: "drop-shadow(0 4px 12px rgba(0,0,0,0.3))",
                background: "transparent",
                mixBlendMode: "multiply",
              }}
            />
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
