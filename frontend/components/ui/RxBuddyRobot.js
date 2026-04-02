/**
 * RxBuddyRobot — Friendly robot mascot SVG with blinking eyes and glow.
 *
 * Designed to sit above the chat drawer, "leaning over" with hands on the edge.
 * Eyes glow with a configurable accent color and blink on a 4s cycle.
 * Subtle floating animation on the whole body.
 *
 * Props:
 *   size      — overall width (default 80)
 *   glowColor — eye/antenna glow color (default "#4a9eff")
 */

import { useId } from "react";

export default function RxBuddyRobot({ size = 80, glowColor = "#4a9eff" }) {
  const uid = useId().replace(/:/g, "");
  const filterId = `glow_${uid}`;

  return (
    <div style={{ width: size, height: size * 0.75, position: "relative" }}>
      <svg
        viewBox="0 0 80 60"
        width={size}
        height={size * 0.75}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ animation: "rxFloat 3s ease-in-out infinite", display: "block" }}
      >
        <defs>
          <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Antenna */}
        <line x1="40" y1="8" x2="40" y2="2" stroke="#e2e8f0" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="40" cy="1.5" r="2" fill={glowColor} filter={`url(#${filterId})`} />

        {/* Head */}
        <rect x="18" y="8" width="44" height="28" rx="12" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1.5" />

        {/* Left eye */}
        <g style={{ animation: "rxBlink 4s infinite", transformOrigin: "32px 22px" }}>
          <rect x="27" y="18" width="10" height="8" rx="3" fill={glowColor} filter={`url(#${filterId})`} />
        </g>

        {/* Right eye */}
        <g style={{ animation: "rxBlink 4s infinite 0.05s", transformOrigin: "48px 22px" }}>
          <rect x="43" y="18" width="10" height="8" rx="3" fill={glowColor} filter={`url(#${filterId})`} />
        </g>

        {/* Mouth — friendly smile arc */}
        <path d="M34 30 Q40 35 46 30" stroke="#e2e8f0" strokeWidth="1.5" fill="none" strokeLinecap="round" />

        {/* Body (extends down, partially clipped by container) */}
        <rect x="28" y="36" width="24" height="16" rx="4" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1.2" />

        {/* "Rx" on body */}
        <text x="40" y="48" fontSize="8" fontFamily="'Inter', system-ui, sans-serif" fill="#e2e8f0" textAnchor="middle" fontWeight="700">Rx</text>

        {/* Left arm — resting on edge */}
        <path d="M28 42 Q20 44 16 52 L18 54 Q22 48 28 46" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1.2" strokeLinejoin="round" />
        <rect x="13" y="52" width="8" height="5" rx="2.5" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1" />

        {/* Right arm — resting on edge */}
        <path d="M52 42 Q60 44 64 52 L62 54 Q58 48 52 46" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1.2" strokeLinejoin="round" />
        <rect x="59" y="52" width="8" height="5" rx="2.5" fill="#0f172a" stroke="#e2e8f0" strokeWidth="1" />
      </svg>

      <style>{`
        @keyframes rxBlink {
          0%, 48%, 52%, 100% { transform: scaleY(1); }
          50% { transform: scaleY(0.1); }
        }
        @keyframes rxFloat {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-2px); }
        }
      `}</style>
    </div>
  );
}
