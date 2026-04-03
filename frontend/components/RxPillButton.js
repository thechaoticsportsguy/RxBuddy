/**
 * RxPillButton — Large 3D spinning pill that opens DrugChatWidget.
 *
 * 480x160 Three.js canvas with a properly shaped black/white capsule.
 * Hover shows animated tooltip. After chat closes, pill hides and a
 * small floating pill emoji button lets user bring it back.
 *
 * Props:
 *   drugName  — passed through to DrugChatWidget
 *   textColor — tooltip text color (default "white")
 */

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import dynamic from "next/dynamic";

const DrugChatWidget = dynamic(() => import("./DrugChatWidget"), { ssr: false });

const CANVAS_W = 480;
const CANVAS_H = 160;
const FONT = "'Inter', system-ui, sans-serif";
const PILL_WRAPPER_ID = "rx-pill-wrapper";

export default function RxPillButton({ drugName, textColor = "white" }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);
  const [isHovered, setIsHovered] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [pillVisible, setPillVisible] = useState(true);
  const [hasBeenOpened, setHasBeenOpened] = useState(false);
  const hoveredRef = useRef(false);

  useEffect(() => { hoveredRef.current = isHovered; }, [isHovered]);

  function handleOpen() {
    setIsTransitioning(true);
    setHasBeenOpened(true);
    setTimeout(() => {
      setIsChatOpen(true);
      setIsTransitioning(false);
    }, 350);
  }

  function handleClose() {
    setIsChatOpen(false);
    // After first open+close, hide the pill
    setPillVisible(false);
  }

  function handleRestorePill() {
    setPillVisible(true);
  }

  useEffect(() => {
    if (isChatOpen || !pillVisible) return;

    let cancelled = false;

    async function init() {
      let THREE;
      try {
        THREE = await import("three");
      } catch {
        return;
      }
      if (cancelled || !mountRef.current) return;

      if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
      }

      const container = mountRef.current;

      // ── Scene ──────────────────────────────────────────────
      const scene = new THREE.Scene();
      scene.background = null;

      const camera = new THREE.PerspectiveCamera(45, CANVAS_W / CANVAS_H, 0.1, 100);
      camera.position.set(0, 0, 4.5);

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

      // ── Left black cap ─────────────────────────────────────
      const leftCapGeo = new THREE.SphereGeometry(0.65, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2);
      const leftCap = new THREE.Mesh(leftCapGeo, leftMat);
      leftCap.rotation.z = Math.PI / 2;
      leftCap.position.x = -0.9;
      pillGroup.add(leftCap);

      // ── Left black cylinder ────────────────────────────────
      const leftCylGeo = new THREE.CylinderGeometry(0.65, 0.65, 0.9, 64, 1, false);
      const leftCyl = new THREE.Mesh(leftCylGeo, leftMatTex);
      leftCyl.rotation.z = Math.PI / 2;
      leftCyl.position.x = -0.45;
      pillGroup.add(leftCyl);

      // ── Right white cylinder ───────────────────────────────
      const rightCylGeo = new THREE.CylinderGeometry(0.65, 0.65, 0.9, 64, 1, false);
      const rightCyl = new THREE.Mesh(rightCylGeo, rightMatTex);
      rightCyl.rotation.z = Math.PI / 2;
      rightCyl.position.x = 0.45;
      pillGroup.add(rightCyl);

      // ── Right white cap ────────────────────────────────────
      const rightCapGeo = new THREE.SphereGeometry(0.65, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2);
      const rightCap = new THREE.Mesh(rightCapGeo, rightMat);
      rightCap.rotation.z = Math.PI / 2;
      rightCap.position.x = 0.9;
      pillGroup.add(rightCap);

      // ── Seam ring ──────────────────────────────────────────
      const seamGeo = new THREE.TorusGeometry(0.652, 0.01, 16, 128);
      const seamMat = new THREE.MeshPhysicalMaterial({
        color: 0x888888,
        roughness: 0.2,
        metalness: 0.4,
      });
      const seam = new THREE.Mesh(seamGeo, seamMat);
      seam.rotation.y = Math.PI / 2;
      pillGroup.add(seam);

      // Tilt the pill
      pillGroup.rotation.z = 0.15;

      // ── Lighting ───────────────────────────────────────────
      scene.add(new THREE.AmbientLight(0xffffff, 0.6));

      const dirLight = new THREE.DirectionalLight(0xffffff, 2);
      dirLight.position.set(3, 3, 5);
      scene.add(dirLight);

      const ptLight = new THREE.PointLight(0xffffff, 1.2);
      ptLight.position.set(-2, 1, 2);
      scene.add(ptLight);

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
  }, [isChatOpen, pillVisible]);

  return (
    <>
      <AnimatePresence mode="wait">
        {/* ── Full 3D pill ──────────────────────────────────── */}
        {!isChatOpen && !isTransitioning && pillVisible && (
          <motion.div
            key="pill"
            id={PILL_WRAPPER_ID}
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
              background: "transparent",
              border: "none",
              boxShadow: "none",
              padding: 0,
              margin: 0,
            }}
          >
            {/* Hover tooltip */}
            <AnimatePresence>
              {isHovered && (
                <motion.span
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: -8 }}
                  exit={{ opacity: 0, y: 10 }}
                  transition={{ duration: 0.3, ease: "easeOut" }}
                  style={{
                    fontFamily: FONT,
                    fontSize: 14,
                    color: textColor,
                    textAlign: "center",
                    pointerEvents: "none",
                    userSelect: "none",
                    position: "absolute",
                    top: -32,
                    left: "50%",
                    transform: "translateX(-50%)",
                    whiteSpace: "nowrap",
                  }}
                >
                  Ask our RxBuddy agent more about your medication
                </motion.span>
              )}
            </AnimatePresence>

            <div
              ref={mountRef}
              style={{
                width: CANVAS_W,
                height: CANVAS_H,
                pointerEvents: "none",
                background: "transparent",
                border: "none",
                boxShadow: "none",
                padding: 0,
                margin: 0,
              }}
            />
          </motion.div>
        )}

        {/* ── Chat drawer ───────────────────────────────────── */}
        {isChatOpen && (
          <DrugChatWidget
            key="chat"
            drugName={drugName}
            isVisible
            onClose={handleClose}
          />
        )}
      </AnimatePresence>

      {/* ── Small restore button (after pill hides) ─────────── */}
      {!pillVisible && !isChatOpen && hasBeenOpened && (
        <motion.button
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.25, ease: "easeOut" }}
          onClick={handleRestorePill}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 999,
            width: 48,
            height: 48,
            borderRadius: "50%",
            background: "rgba(10,15,30,0.85)",
            border: "1px solid rgba(255,255,255,0.15)",
            color: "#fff",
            fontSize: 22,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
            transition: "background 0.15s, transform 0.15s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "rgba(10,15,30,1)";
            e.currentTarget.style.transform = "scale(1.1)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "rgba(10,15,30,0.85)";
            e.currentTarget.style.transform = "scale(1)";
          }}
          aria-label="Reopen RxBuddy pill"
        >
          {"\uD83D\uDC8A"}
        </motion.button>
      )}
    </>
  );
}
