/**
 * RxBuddyRobot — Chat CTA card button (bottom-right, fixed).
 *
 * Renders as a rounded dark-gradient card with the robot image on the
 * left and two-line text on the right. Hides when chat is open.
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
      {/* ── Chat CTA card (hidden when chat is open) ───────────── */}
      <AnimatePresence>
        {!isChatOpen && (
          <motion.div
            key="cta-card"
            id="rx-pill-wrapper"
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 20, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeOut" }}
            onClick={() => setIsChatOpen(true)}
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
            style={{
              position: "fixed",
              bottom: 24,
              right: 24,
              zIndex: 999,
              cursor: "pointer",
              width: 240,
              height: 72,
              borderRadius: 16,
              background: "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)",
              border: "1px solid rgba(74,158,255,0.25)",
              boxShadow: isHovered
                ? "0 12px 36px rgba(0,0,0,0.55), 0 0 0 1px rgba(74,158,255,0.4)"
                : "0 8px 24px rgba(0,0,0,0.4)",
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "0 16px",
              userSelect: "none",
              transform: isHovered ? "scale(1.03)" : "scale(1)",
              transition: "transform 0.15s ease, box-shadow 0.15s ease",
            }}
          >
            {/* Robot image */}
            <div
              style={{
                width: 44,
                height: 44,
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <img
                src="/rxbuddy-robot.png"
                alt="RxBuddy"
                draggable={false}
                style={{
                  width: 44,
                  height: 44,
                  objectFit: "contain",
                  display: "block",
                }}
              />
            </div>

            {/* Text */}
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 14,
                  fontWeight: 700,
                  color: "white",
                  lineHeight: 1.2,
                }}
              >
                Ask me any questions
              </span>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  fontWeight: 400,
                  color: "rgba(255,255,255,0.65)",
                  lineHeight: 1.2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 140,
                }}
              >
                about {drugName || "your medication"}
              </span>
            </div>

            {/* Arrow indicator */}
            <div
              style={{
                marginLeft: "auto",
                color: "rgba(74,158,255,0.8)",
                fontSize: 18,
                flexShrink: 0,
              }}
            >
              &#x2192;
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
    </>
  );
}
