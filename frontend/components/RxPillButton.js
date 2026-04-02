/**
 * RxPillButton — 3D Three.js spinning pill that opens DrugChatWidget.
 *
 * Renders a small inline 220x80 canvas with a realistic black/white
 * capsule pill that rotates continuously. On hover it spins faster
 * and scales up. On click it opens the DrugChatWidget chat drawer.
 *
 * Props:
 *   drugName  — passed through to DrugChatWidget
 *   textColor — color of the label text above the pill (default "white")
 */

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

const DrugChatWidget = dynamic(() => import("./DrugChatWidget"), { ssr: false });

const CANVAS_W = 220;
const CANVAS_H = 80;

export default function RxPillButton({ drugName, textColor = "white" }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);
  const [isHovered, setIsHovered] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const hoveredRef = useRef(false);

  // Keep ref in sync so the animation loop reads the latest value
  useEffect(() => { hoveredRef.current = isHovered; }, [isHovered]);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      let THREE;
      try {
        THREE = await import("three");
      } catch {
        return;
      }
      if (cancelled || !mountRef.current) return;

      const container = mountRef.current;

      // ── Scene ──────────────────────────────────────────────────
      const scene = new THREE.Scene();
      // transparent background — alpha renderer
      scene.background = null;

      const camera = new THREE.PerspectiveCamera(45, CANVAS_W / CANVAS_H, 0.1, 100);
      camera.position.set(0, 0, 3.5);

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setSize(CANVAS_W, CANVAS_H);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      container.appendChild(renderer.domElement);

      // ── Pill group ─────────────────────────────────────────────
      const pillGroup = new THREE.Group();
      scene.add(pillGroup);

      // Left-half material (black)
      const leftMat = new THREE.MeshPhysicalMaterial({
        color: 0x000000,
        roughness: 0.1,
        metalness: 0.05,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      // Right-half material (white)
      const rightMat = new THREE.MeshPhysicalMaterial({
        color: 0xffffff,
        roughness: 0.1,
        metalness: 0.05,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      // ── "RxBuddy" texture ──────────────────────────────────────
      const txCanvas = document.createElement("canvas");
      txCanvas.width = 512;
      txCanvas.height = 128;
      const txCtx = txCanvas.getContext("2d");
      // Left half black, right half white
      txCtx.fillStyle = "#000000";
      txCtx.fillRect(0, 0, 256, 128);
      txCtx.fillStyle = "#ffffff";
      txCtx.fillRect(256, 0, 256, 128);
      // Text — white on left, black on right
      txCtx.font = "bold 48px 'Times New Roman', Times, serif";
      txCtx.textAlign = "center";
      txCtx.textBaseline = "middle";
      txCtx.fillStyle = "#ffffff";
      txCtx.fillText("Rx", 128, 64);
      txCtx.fillStyle = "#000000";
      txCtx.fillText("Buddy", 384, 64);
      const rxTex = new THREE.CanvasTexture(txCanvas);

      // Clone materials with texture for the cylinders
      const leftMatTex = leftMat.clone();
      leftMatTex.map = rxTex;
      const rightMatTex = rightMat.clone();
      rightMatTex.map = rxTex;

      // ── Left hemisphere (cap) ──────────────────────────────────
      const leftHemiGeo = new THREE.SphereGeometry(
        0.5, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2
      );
      const leftHemi = new THREE.Mesh(leftHemiGeo, leftMat);
      leftHemi.rotation.z = Math.PI / 2;
      leftHemi.position.x = -0.6;
      pillGroup.add(leftHemi);

      // ── Left cylinder (barrel) ─────────────────────────────────
      const leftCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
      const leftCyl = new THREE.Mesh(leftCylGeo, leftMatTex);
      leftCyl.rotation.z = Math.PI / 2;
      leftCyl.position.x = 0;
      pillGroup.add(leftCyl);

      // ── Right hemisphere (cap) ─────────────────────────────────
      const rightHemiGeo = new THREE.SphereGeometry(
        0.5, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2
      );
      const rightHemi = new THREE.Mesh(rightHemiGeo, rightMat);
      rightHemi.rotation.z = Math.PI / 2;
      rightHemi.position.x = 0.6;
      pillGroup.add(rightHemi);

      // ── Right cylinder (barrel) ────────────────────────────────
      const rightCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
      const rightCyl = new THREE.Mesh(rightCylGeo, rightMatTex);
      rightCyl.rotation.z = Math.PI / 2;
      rightCyl.position.x = 0;
      pillGroup.add(rightCyl);

      // ── Seam ring ──────────────────────────────────────────────
      const seamGeo = new THREE.TorusGeometry(0.502, 0.008, 16, 128);
      const seamMat = new THREE.MeshPhysicalMaterial({
        color: 0x888888,
        roughness: 0.2,
        metalness: 0.4,
      });
      const seam = new THREE.Mesh(seamGeo, seamMat);
      seam.rotation.y = Math.PI / 2;
      pillGroup.add(seam);

      // ── Lighting ───────────────────────────────────────────────
      scene.add(new THREE.AmbientLight(0xffffff, 0.6));

      const dirLight = new THREE.DirectionalLight(0xffffff, 2);
      dirLight.position.set(3, 3, 5);
      scene.add(dirLight);

      const ptLight = new THREE.PointLight(0xffffff, 1.2);
      ptLight.position.set(-2, 1, 2);
      scene.add(ptLight);

      // ── Animation loop ─────────────────────────────────────────
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

      // ── Cleanup ────────────────────────────────────────────────
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
      if (cleanupRef.current) cleanupRef.current();
    };
  }, []);

  return (
    <>
      <div
        onClick={() => setIsChatOpen(true)}
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
          gap: 4,
        }}
      >
        <span style={{
          fontFamily: "'Times New Roman', Times, serif",
          fontSize: 13,
          color: textColor,
          textAlign: "center",
          pointerEvents: "none",
          userSelect: "none",
        }}>
          Ask RxBuddy Assistant more about your medication
        </span>
        <div
          ref={mountRef}
          style={{ width: CANVAS_W, height: CANVAS_H, pointerEvents: "none" }}
        />
      </div>

      {isChatOpen && (
        <DrugChatWidget
          drugName={drugName}
          isVisible
        />
      )}
    </>
  );
}
