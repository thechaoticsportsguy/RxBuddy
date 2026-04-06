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
  const [isHovered, setIsHovered] = useState(false);

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
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
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
            {/* Hover tooltip */}
            <AnimatePresence>
              {isHovered && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 8 }}
                  transition={{ duration: 0.25, ease: "easeOut" }}
                  style={{
                    position: "absolute",
                    bottom: "100%",
                    marginBottom: 8,
                    background: "#111",
                    borderRadius: 8,
                    padding: "8px 12px",
                    whiteSpace: "nowrap",
                    pointerEvents: "none",
                  }}
                >
                  <span style={{
                    fontFamily: FONT,
                    fontSize: 13,
                    color: "#fff",
                  }}>
                    {"Ask me anything about \u201C" + (drugName || "your medication") + "\u201D"}
                  </span>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Robot image */}
            <motion.img
              src="/rxbuddy-robot.png"
              alt="RxBuddy Robot"
              draggable={false}
              animate={{
                y: [0, -6, 0],
                scale: isHovered ? 1.08 : 1,
              }}
              transition={{
                y: { duration: 2, repeat: Infinity, ease: "easeInOut" },
                scale: { duration: 0.2 },
              }}
              style={{
                width: 100,
                height: "auto",
                userSelect: "none",
                filter: "drop-shadow(0 4px 12px rgba(0,0,0,0.3))",
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
