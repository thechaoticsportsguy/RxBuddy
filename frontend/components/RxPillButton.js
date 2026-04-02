/**
 * RxPillButton — Large 3D spinning pill that opens DrugChatWidget.
 *
 * 320x110 Three.js canvas with a black/white capsule, Inter font,
 * framer-motion transitions between pill and chat states. Clicking
 * the pill smoothly transitions to the chat drawer; closing the
 * chat brings the pill back.
 *
 * Props:
 *   drugName  — passed through to DrugChatWidget
 *   textColor — label text color (default "white")
 */

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import dynamic from "next/dynamic";

const DrugChatWidget = dynamic(() => import("./DrugChatWidget"), { ssr: false });

const CANVAS_W = 320;
const CANVAS_H = 110;
const FONT = "'Inter', system-ui, sans-serif";

export default function RxPillButton({ drugName, textColor = "white" }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);
  const [isHovered, setIsHovered] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const hoveredRef = useRef(false);

  useEffect(() => { hoveredRef.current = isHovered; }, [isHovered]);

  function handleOpen() {
    setIsTransitioning(true);
    setTimeout(() => {
      setIsChatOpen(true);
      setIsTransitioning(false);
    }, 350);
  }

  function handleClose() {
    setIsChatOpen(false);
  }

  useEffect(() => {
    if (isChatOpen) return; // don't init Three.js while chat is open

    let cancelled = false;

    async function init() {
      let THREE;
      try {
        THREE = await import("three");
      } catch {
        return;
      }
      if (cancelled || !mountRef.current) return;

      // Wait for Inter font to load before drawing canvas texture
      if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
      }

      const container = mountRef.current;

      // ── Scene ──────────────────────────────────────────────
      const scene = new THREE.Scene();
      scene.background = null;

      const camera = new THREE.PerspectiveCamera(45, CANVAS_W / CANVAS_H, 0.1, 100);
      camera.position.set(0, 0, 4.0);

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setSize(CANVAS_W, CANVAS_H);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      container.appendChild(renderer.domElement);

      // ── Pill group ─────────────────────────────────────────
      const pillGroup = new THREE.Group();
      scene.add(pillGroup);

      const leftMat = new THREE.MeshPhysicalMaterial({
        color: 0x000000,
        roughness: 0.1,
        metalness: 0.05,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      const rightMat = new THREE.MeshPhysicalMaterial({
        color: 0xffffff,
        roughness: 0.1,
        metalness: 0.05,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      // ── "RxBuddy" texture ──────────────────────────────────
      const txCanvas = document.createElement("canvas");
      txCanvas.width = 512;
      txCanvas.height = 128;
      const txCtx = txCanvas.getContext("2d");
      txCtx.fillStyle = "#000000";
      txCtx.fillRect(0, 0, 256, 128);
      txCtx.fillStyle = "#ffffff";
      txCtx.fillRect(256, 0, 256, 128);
      txCtx.font = "bold 48px 'Inter', system-ui, sans-serif";
      txCtx.textAlign = "center";
      txCtx.textBaseline = "middle";
      txCtx.fillStyle = "#ffffff";
      txCtx.fillText("Rx", 128, 64);
      txCtx.fillStyle = "#000000";
      txCtx.fillText("Buddy", 384, 64);
      const rxTex = new THREE.CanvasTexture(txCanvas);

      const leftMatTex = leftMat.clone();
      leftMatTex.map = rxTex;
      const rightMatTex = rightMat.clone();
      rightMatTex.map = rxTex;

      // ── Left hemisphere ────────────────────────────────────
      const leftHemiGeo = new THREE.SphereGeometry(0.5, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2);
      const leftHemi = new THREE.Mesh(leftHemiGeo, leftMat);
      leftHemi.rotation.z = Math.PI / 2;
      leftHemi.position.x = -0.6;
      pillGroup.add(leftHemi);

      // ── Left cylinder ──────────────────────────────────────
      const leftCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
      const leftCyl = new THREE.Mesh(leftCylGeo, leftMatTex);
      leftCyl.rotation.z = Math.PI / 2;
      leftCyl.position.x = 0;
      pillGroup.add(leftCyl);

      // ── Right hemisphere ───────────────────────────────────
      const rightHemiGeo = new THREE.SphereGeometry(0.5, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2);
      const rightHemi = new THREE.Mesh(rightHemiGeo, rightMat);
      rightHemi.rotation.z = Math.PI / 2;
      rightHemi.position.x = 0.6;
      pillGroup.add(rightHemi);

      // ── Right cylinder ─────────────────────────────────────
      const rightCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
      const rightCyl = new THREE.Mesh(rightCylGeo, rightMatTex);
      rightCyl.rotation.z = Math.PI / 2;
      rightCyl.position.x = 0;
      pillGroup.add(rightCyl);

      // ── Seam ring ──────────────────────────────────────────
      const seamGeo = new THREE.TorusGeometry(0.502, 0.008, 16, 128);
      const seamMat = new THREE.MeshPhysicalMaterial({
        color: 0x888888,
        roughness: 0.2,
        metalness: 0.4,
      });
      const seam = new THREE.Mesh(seamGeo, seamMat);
      seam.rotation.y = Math.PI / 2;
      pillGroup.add(seam);

      // ── Lighting ───────────────────────────────────────────
      scene.add(new THREE.AmbientLight(0xffffff, 0.6));

      const dirLight = new THREE.DirectionalLight(0xffffff, 2);
      dirLight.position.set(3, 3, 5);
      scene.add(dirLight);

      const ptLight = new THREE.PointLight(0xffffff, 1.2);
      ptLight.position.set(-2, 1, 2);
      scene.add(ptLight);

      // Blue rim light for accent glow
      const rimLight = new THREE.PointLight(0x4a9eff, 0.3);
      rimLight.position.set(0, 0, -2);
      scene.add(rimLight);

      // ── Animation loop ─────────────────────────────────────
      let hoverScale = 1;
      let animId;

      function animate() {
        animId = requestAnimationFrame(animate);

        const speed = hoveredRef.current ? 0.04 : 0.012;
        pillGroup.rotation.y += speed;

        const targetScale = hoveredRef.current ? 1.12 : 1.0;
        hoverScale += (targetScale - hoverScale) * 0.08;
        pillGroup.scale.setScalar(hoverScale);

        renderer.render(scene, camera);
      }

      animate();

      // ── Cleanup ────────────────────────────────────────────
      cleanupRef.current = () => {
        cancelAnimationFrame(animId);
        scene.traverse((obj) => {
          if (obj.geometry) obj.geometry.dispose();
          if (obj.material) {
            const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
            mats.forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); });
          }
        });
        renderer.dispose();
        if (container.contains(renderer.domElement)) {
          container.removeChild(renderer.domElement);
        }
      };
    }

    init();
    return () => {
      cancelled = true;
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
    };
  }, [isChatOpen]);

  return (
    <AnimatePresence mode="wait">
      {!isChatOpen && !isTransitioning && (
        <motion.div
          key="pill"
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 1.15 }}
          transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
          onClick={handleOpen}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 999,
            cursor: "pointer",
            display: "inline-flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 6,
            boxShadow: isHovered ? "0 0 30px rgba(74,158,255,0.2)" : "none",
            borderRadius: 16,
            padding: "10px 14px 6px",
            transition: "box-shadow 0.3s ease",
          }}
        >
          <span style={{
            fontFamily: FONT,
            fontSize: 14,
            fontWeight: 500,
            letterSpacing: "-0.01em",
            color: textColor,
            textAlign: "center",
            pointerEvents: "none",
            userSelect: "none",
          }}>
            Ask RxBuddy about your medication
          </span>
          <div
            ref={mountRef}
            style={{ width: CANVAS_W, height: CANVAS_H, pointerEvents: "none" }}
          />
        </motion.div>
      )}

      {isChatOpen && (
        <DrugChatWidget
          key="chat"
          drugName={drugName}
          isVisible
          onClose={handleClose}
        />
      )}
    </AnimatePresence>
  );
}
