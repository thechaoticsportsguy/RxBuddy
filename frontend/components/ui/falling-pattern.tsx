"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";

interface FallingPatternProps {
  color?: string;
  backgroundColor?: string;
  patternSize?: number;
  count?: number;
  speed?: number;
  className?: string;
}

interface Particle {
  id: number;
  x: number;
  delay: number;
  duration: number;
  size: number;
  opacity: number;
  char: string;
  rotation: number;
}

const CHARS = [
  "\u2295", "\u2296", "\u2297", "\u2299", "\u229B",  // circled operators
  "\u25CB", "\u25CF", "\u25C6", "\u25C7", "\u25B3",  // geometric shapes
  "\u2726", "\u2727", "\u2728", "\u2736", "\u273A",  // stars
  "\u2022", "\u25AA", "\u25AB", "\u25B2", "\u25BC",  // bullets & arrows
  "Rx", "\u2695", "\u269B", "\u2318", "\u2302",      // medical/misc symbols
];

export default function FallingPattern({
  color = "hsl(200, 100%, 60%)",
  backgroundColor = "transparent",
  patternSize = 20,
  count = 50,
  speed = 1,
  className = "",
}: FallingPatternProps) {
  const [dimensions, setDimensions] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const update = () =>
      setDimensions({ w: window.innerWidth, h: window.innerHeight });
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  const particles: Particle[] = useMemo(() => {
    if (!dimensions.w) return [];
    return Array.from({ length: count }, (_, i) => ({
      id: i,
      x: Math.random() * 100,
      delay: Math.random() * 15,
      duration: (8 + Math.random() * 12) / speed,
      size: patternSize * (0.5 + Math.random() * 0.8),
      opacity: 0.05 + Math.random() * 0.2,
      char: CHARS[Math.floor(Math.random() * CHARS.length)],
      rotation: Math.random() * 360,
    }));
  }, [dimensions.w, count, patternSize, speed]);

  if (!dimensions.w) return null;

  return (
    <div
      className={className}
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        backgroundColor,
        pointerEvents: "none",
      }}
    >
      {particles.map((p) => (
        <motion.div
          key={p.id}
          initial={{ y: "-10%", rotate: p.rotation, opacity: 0 }}
          animate={{
            y: "110vh",
            rotate: p.rotation + 180,
            opacity: [0, p.opacity, p.opacity, 0],
          }}
          transition={{
            duration: p.duration,
            delay: p.delay,
            repeat: Infinity,
            ease: "linear",
          }}
          style={{
            position: "absolute",
            left: `${p.x}%`,
            fontSize: p.size,
            color,
            userSelect: "none",
            willChange: "transform",
          }}
        >
          {p.char}
        </motion.div>
      ))}
    </div>
  );
}
