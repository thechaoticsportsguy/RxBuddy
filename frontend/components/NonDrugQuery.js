/**
 * NonDrugQuery — Premium 3D pill with rainbow chromatic animation.
 *
 * Renders a realistic pharmaceutical capsule using Three.js with:
 *   - MeshPhysicalMaterial clearcoat for glossy finish
 *   - Rainbow HSL color cycling on both halves
 *   - Mouse-tracking rotation (follows cursor)
 *   - Idle floating animation + hover scale
 *   - 60 orbiting sparkle particles with twinkle
 *   - Soft glow sprites behind pill
 *   - "RxBuddy" canvas texture engraving
 *   - CSS overlay text (pointer-events: none)
 *
 * Props:
 *   query     — the user's original search string
 *   isIllegal — if true, shows SAMHSA helpline
 *   message   — optional custom message override
 */

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

const FallingPattern = dynamic(() => import("./ui/falling-pattern"), {
  ssr: false,
});

export default function NonDrugQuery({ query, isIllegal = false, message }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);
  const [hue, setHue] = useState(200);

  // Rainbow hue cycling for FallingPattern
  useEffect(() => {
    let raf;
    let start = performance.now();
    function tick() {
      const elapsed = (performance.now() - start) / 1000;
      setHue(Math.round((elapsed * 36) % 360)); // full cycle every 10s
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      let THREE;
      try {
        THREE = await import("three");
      } catch {
        console.warn("[NonDrugQuery] Three.js not available");
        return;
      }
      if (cancelled || !mountRef.current) return;

      const container = mountRef.current;
      const width = container.clientWidth || window.innerWidth;
      const height = container.clientHeight || window.innerHeight;

      // ── Scene (transparent so FallingPattern shows behind) ──────────
      const scene = new THREE.Scene();
      scene.background = null;

      const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
      camera.position.set(0, 0, 6);

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.2;
      container.appendChild(renderer.domElement);

      // ── Pill Capsule Group ───────────────────────────────────────────
      const pillGroup = new THREE.Group();
      scene.add(pillGroup);

      // Left-half material (pink → rainbow)
      const leftMat = new THREE.MeshPhysicalMaterial({
        color: 0xe8a0bf,
        roughness: 0.1,
        metalness: 0.05,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      // Right-half material (dark → rainbow)
      const rightMat = new THREE.MeshPhysicalMaterial({
        color: 0x1a1a2e,
        roughness: 0.1,
        metalness: 0.1,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
      });

      // ── "RxBuddy" engraving texture ──────────────────────────────────
      const rxCanvas = document.createElement("canvas");
      rxCanvas.width = 512;
      rxCanvas.height = 512;
      const rxCtx = rxCanvas.getContext("2d");
      rxCtx.clearRect(0, 0, 512, 512);
      rxCtx.font = "bold 120px Inter, system-ui, sans-serif";
      rxCtx.textAlign = "center";
      rxCtx.textBaseline = "middle";
      rxCtx.fillStyle = "rgba(255, 255, 255, 0.45)";
      rxCtx.fillText("RxBuddy", 256, 256);
      const rxTex = new THREE.CanvasTexture(rxCanvas);

      const rightMatEngrave = rightMat.clone();
      rightMatEngrave.map = rxTex;

      // ── Build pill geometry ──────────────────────────────────────────
      // Try CapsuleGeometry first (Three.js r128+), fall back to manual
      let usedCapsule = false;
      if (THREE.CapsuleGeometry) {
        try {
          const leftCapsuleGeo = new THREE.CapsuleGeometry(0.5, 1.2, 16, 32);
          const leftCapsule = new THREE.Mesh(leftCapsuleGeo, leftMat);
          leftCapsule.rotation.z = Math.PI / 2;
          leftCapsule.position.x = -0.6;
          leftCapsule.castShadow = true;
          pillGroup.add(leftCapsule);

          const rightCapsuleGeo = new THREE.CapsuleGeometry(0.5, 1.2, 16, 32);
          const rightCapsule = new THREE.Mesh(rightCapsuleGeo, rightMatEngrave);
          rightCapsule.rotation.z = Math.PI / 2;
          rightCapsule.position.x = 0.6;
          rightCapsule.castShadow = true;
          pillGroup.add(rightCapsule);

          usedCapsule = true;
        } catch {
          usedCapsule = false;
        }
      }

      if (!usedCapsule) {
        // ── Left hemisphere (cap) ──────────────────────────────────────
        const leftHemiGeo = new THREE.SphereGeometry(
          0.5, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2
        );
        const leftHemi = new THREE.Mesh(leftHemiGeo, leftMat);
        leftHemi.rotation.z = Math.PI / 2;
        leftHemi.position.x = -0.6;
        leftHemi.castShadow = true;
        pillGroup.add(leftHemi);

        // ── Left cylinder (barrel) ─────────────────────────────────────
        const leftCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
        const leftCyl = new THREE.Mesh(leftCylGeo, leftMat);
        leftCyl.rotation.z = Math.PI / 2;
        leftCyl.position.x = 0;
        leftCyl.castShadow = true;
        pillGroup.add(leftCyl);

        // ── Right hemisphere (cap) ─────────────────────────────────────
        const rightHemiGeo = new THREE.SphereGeometry(
          0.5, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2
        );
        const rightHemi = new THREE.Mesh(rightHemiGeo, rightMatEngrave);
        rightHemi.rotation.z = Math.PI / 2;
        rightHemi.position.x = 0.6;
        rightHemi.castShadow = true;
        pillGroup.add(rightHemi);

        // ── Right cylinder (barrel) ────────────────────────────────────
        const rightCylGeo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 64, 1, false);
        const rightCyl = new THREE.Mesh(rightCylGeo, rightMatEngrave);
        rightCyl.rotation.z = Math.PI / 2;
        rightCyl.position.x = 0;
        rightCyl.castShadow = true;
        pillGroup.add(rightCyl);
      }

      // ── Seam ring between halves ─────────────────────────────────────
      const seamGeo = new THREE.TorusGeometry(0.502, 0.008, 16, 128);
      const seamMat = new THREE.MeshPhysicalMaterial({
        color: 0x8899ff,
        roughness: 0.1,
        metalness: 0.6,
        emissive: 0x3355ff,
        emissiveIntensity: 0.4,
      });
      const seam = new THREE.Mesh(seamGeo, seamMat);
      seam.rotation.y = Math.PI / 2;
      pillGroup.add(seam);

      // Tilt the pill
      pillGroup.rotation.z = 0.2;

      // ── Grid Background ──────────────────────────────────────────────
      const gridCanvas = document.createElement("canvas");
      gridCanvas.width = 512;
      gridCanvas.height = 512;
      const gCtx = gridCanvas.getContext("2d");
      gCtx.fillStyle = "#060c1a";
      gCtx.fillRect(0, 0, 512, 512);
      gCtx.strokeStyle = "rgba(30, 60, 140, 0.22)";
      gCtx.lineWidth = 1;
      for (let x = 0; x <= 512; x += 32) {
        gCtx.beginPath(); gCtx.moveTo(x, 0); gCtx.lineTo(x, 512); gCtx.stroke();
      }
      for (let y = 0; y <= 512; y += 32) {
        gCtx.beginPath(); gCtx.moveTo(0, y); gCtx.lineTo(512, y); gCtx.stroke();
      }
      const gridTex = new THREE.CanvasTexture(gridCanvas);
      gridTex.wrapS = THREE.RepeatWrapping;
      gridTex.wrapT = THREE.RepeatWrapping;
      gridTex.repeat.set(6, 4);
      const gridPlane = new THREE.Mesh(
        new THREE.PlaneGeometry(40, 28),
        new THREE.MeshBasicMaterial({ map: gridTex, transparent: true, opacity: 1, depthWrite: false })
      );
      gridPlane.position.z = -5;
      scene.add(gridPlane);

      // ── Glow Sprites ─────────────────────────────────────────────────
      function makeGlow(color, size, opacity) {
        const c = document.createElement("canvas");
        c.width = 256; c.height = 256;
        const ctx = c.getContext("2d");
        const grad = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
        grad.addColorStop(0, "rgba(" + color + "," + opacity + ")");
        grad.addColorStop(0.4, "rgba(" + color + "," + (opacity * 0.4) + ")");
        grad.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 256, 256);
        const tex = new THREE.CanvasTexture(c);
        const mat = new THREE.SpriteMaterial({
          map: tex, transparent: true, depthWrite: false,
          blending: THREE.AdditiveBlending,
        });
        const sprite = new THREE.Sprite(mat);
        sprite.scale.set(size, size, 1);
        return sprite;
      }

      const glowA = makeGlow("60,100,255", 6.5, 0.55);
      glowA.position.x = -0.5;
      scene.add(glowA);

      const glowB = makeGlow("200,210,255", 5.5, 0.35);
      glowB.position.x = 0.5;
      scene.add(glowB);

      const glowOuter = makeGlow("40,70,220", 9, 0.2);
      scene.add(glowOuter);

      // ── Sparkle Particles (60) ───────────────────────────────────────
      const sparkleCount = 60;
      const sparkleGeo = new THREE.BufferGeometry();
      const sparklePos = new Float32Array(sparkleCount * 3);
      const sparklePhases = new Float32Array(sparkleCount);
      const sparkleOrbits = [];

      for (let i = 0; i < sparkleCount; i++) {
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 2.2 + Math.random() * 2.0;
        sparklePos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        sparklePos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        sparklePos[i * 3 + 2] = r * Math.cos(phi);
        sparklePhases[i] = Math.random() * Math.PI * 2;
        sparkleOrbits.push({ r, theta, phi, speed: (Math.random() - 0.5) * 0.3 });
      }
      sparkleGeo.setAttribute("position", new THREE.BufferAttribute(sparklePos, 3));

      // 4-point star texture
      const sparkCanvas = document.createElement("canvas");
      sparkCanvas.width = 64; sparkCanvas.height = 64;
      const sCtx = sparkCanvas.getContext("2d");
      sCtx.clearRect(0, 0, 64, 64);
      sCtx.save(); sCtx.translate(32, 32);
      for (let a = 0; a < 4; a++) {
        sCtx.save(); sCtx.rotate((a * Math.PI) / 2);
        const grad = sCtx.createLinearGradient(0, -28, 0, 28);
        grad.addColorStop(0, "rgba(255,255,255,0)");
        grad.addColorStop(0.5, "rgba(255,255,255,1)");
        grad.addColorStop(1, "rgba(255,255,255,0)");
        sCtx.fillStyle = grad;
        sCtx.beginPath();
        sCtx.ellipse(0, 0, 28 * 0.12, 28, 0, 0, Math.PI * 2);
        sCtx.fill(); sCtx.restore();
      }
      const cg = sCtx.createRadialGradient(0, 0, 0, 0, 0, 11);
      cg.addColorStop(0, "rgba(200,220,255,1)");
      cg.addColorStop(1, "rgba(200,220,255,0)");
      sCtx.fillStyle = cg;
      sCtx.beginPath(); sCtx.arc(0, 0, 11, 0, Math.PI * 2); sCtx.fill();
      sCtx.restore();
      const sparkleTex = new THREE.CanvasTexture(sparkCanvas);

      const sparkleMat = new THREE.PointsMaterial({
        map: sparkleTex, size: 0.12, sizeAttenuation: true,
        transparent: true, depthWrite: false,
        blending: THREE.AdditiveBlending,
        color: 0xaaccff, opacity: 0.9,
      });
      const sparkles = new THREE.Points(sparkleGeo, sparkleMat);
      scene.add(sparkles);

      // ── Lighting ─────────────────────────────────────────────────────
      scene.add(new THREE.AmbientLight(0xffffff, 0.4));

      const dirLight = new THREE.DirectionalLight(0xffffff, 2);
      dirLight.position.set(3, 3, 5);
      dirLight.castShadow = true;
      scene.add(dirLight);

      const pointLight = new THREE.PointLight(0x8888ff, 1.5);
      pointLight.position.set(-2, 2, 2);
      scene.add(pointLight);

      // RectAreaLight if available
      try {
        if (THREE.RectAreaLight) {
          const rectLight = new THREE.RectAreaLight(0xffffff, 1, 4, 4);
          rectLight.position.set(0, 2, 3);
          rectLight.lookAt(0, 0, 0);
          scene.add(rectLight);
        }
      } catch {
        // RectAreaLight not available — skip
      }

      // ── Mouse Tracking ───────────────────────────────────────────────
      let targetX = 0;
      let targetY = 0;
      let isHovered = false;
      let hoverScale = 1;

      const onMouseMove = (e) => {
        const rect = container.getBoundingClientRect();
        const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const ny = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        targetY = nx * 0.5;
        targetX = ny * 0.3;

        // Check hover on pill bounding area
        const cx = (e.clientX - rect.left) / rect.width;
        const cy = (e.clientY - rect.top) / rect.height;
        isHovered = (Math.abs(cx - 0.5) < 0.2 && Math.abs(cy - 0.5) < 0.2);
      };

      const onMouseLeave = () => {
        targetX = 0;
        targetY = 0;
        isHovered = false;
      };

      window.addEventListener("mousemove", onMouseMove);
      container.addEventListener("mouseleave", onMouseLeave);

      // ── Animation Loop ───────────────────────────────────────────────
      const clock = new THREE.Clock();
      let animId;

      function animate() {
        animId = requestAnimationFrame(animate);
        const t = clock.getElapsedTime();

        // Mouse-following rotation (smooth lerp)
        pillGroup.rotation.y += (targetY - pillGroup.rotation.y) * 0.05;
        pillGroup.rotation.x += (targetX - pillGroup.rotation.x) * 0.05;

        // Idle float
        pillGroup.position.y = Math.sin(t * 0.8) * 0.15;

        // Hover scale (smooth lerp to 1.08)
        const targetScale = isHovered ? 1.08 : 1.0;
        hoverScale += (targetScale - hoverScale) * 0.05;
        pillGroup.scale.setScalar(hoverScale);

        // ── Rainbow color cycling ────────────────────────────────────
        leftMat.color.setHSL((t * 0.1) % 1, 0.8, 0.65);
        rightMat.color.setHSL(((t * 0.1) + 0.5) % 1, 0.8, 0.3);
        rightMatEngrave.color.setHSL(((t * 0.1) + 0.5) % 1, 0.8, 0.3);

        // Seam follows rainbow
        const seamHue = ((t * 0.1) + 0.25) % 1;
        seamMat.color.setHSL(seamHue, 1, 0.6);
        seamMat.emissive.setHSL(seamHue, 1, 0.3);

        // Sparkle color shifts
        sparkleMat.color.setHSL(((t * 0.1) + 0.33) % 1, 0.8, 0.8);

        // Glow pulse
        const glowPulse = 1 + Math.sin(t * 3) * 0.15;
        glowA.scale.setScalar(6.5 * glowPulse);
        glowB.scale.setScalar(5.5 * glowPulse);
        glowOuter.scale.setScalar(9 * (1 + Math.sin(t * 1.5) * 0.08));
        glowA.position.y = pillGroup.position.y;
        glowB.position.y = pillGroup.position.y;
        glowOuter.position.y = pillGroup.position.y;

        // Animate sparkle orbits + twinkling opacity
        const pos = sparkleGeo.attributes.position.array;
        for (let i = 0; i < sparkleCount; i++) {
          const orb = sparkleOrbits[i];
          orb.theta += orb.speed * 0.008;
          pos[i * 3] = orb.r * Math.sin(orb.phi) * Math.cos(orb.theta);
          pos[i * 3 + 1] = orb.r * Math.sin(orb.phi) * Math.sin(orb.theta) + pillGroup.position.y * 0.3;
          pos[i * 3 + 2] = orb.r * Math.cos(orb.phi);
        }
        sparkleGeo.attributes.position.needsUpdate = true;
        // Twinkling: each particle oscillates at its own phase
        sparkleMat.opacity = 0.5 + 0.5 * Math.sin(t * 2.5);

        // Point light pulses
        pointLight.intensity = 1.5 + Math.sin(t * 2) * 0.5;

        renderer.render(scene, camera);
      }

      animate();

      // ── Resize ───────────────────────────────────────────────────────
      const onResize = () => {
        if (!container) return;
        const w = container.clientWidth || window.innerWidth;
        const h = container.clientHeight || window.innerHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      };
      window.addEventListener("resize", onResize);

      // ── Cleanup ──────────────────────────────────────────────────────
      cleanupRef.current = () => {
        cancelAnimationFrame(animId);
        window.removeEventListener("mousemove", onMouseMove);
        container.removeEventListener("mouseleave", onMouseLeave);
        window.removeEventListener("resize", onResize);
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
    <div style={{
      position: "fixed",
      inset: 0,
      zIndex: 50,
      background: "#060c1a",
      overflow: "hidden",
    }}>
      {/* Layer 0: Falling pattern background */}
      <div style={{ position: "fixed", inset: 0, zIndex: 0 }}>
        <FallingPattern
          color={`hsl(${hue}, 100%, 60%)`}
          backgroundColor="transparent"
          count={50}
          speed={0.8}
        />
      </div>

      {/* Layer 1: Three.js canvas */}
      <div
        ref={mountRef}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 1,
          overflow: "hidden",
        }}
      />

      {/* Layer 2: UI Overlay (CSS over canvas, pointer-events: none) */}
      <div style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "flex-start",
        pointerEvents: "none",
        userSelect: "none",
        fontFamily: "'Inter', system-ui, sans-serif",
        zIndex: 2,
      }}>
        {/* Top center heading */}
        <h2 style={{
          marginTop: 48,
          fontSize: "3.2rem",
          fontWeight: 900,
          color: "#ffffff",
          letterSpacing: "-0.01em",
          textAlign: "center",
          lineHeight: 1.15,
          padding: "0 24px",
          textShadow: "0 2px 20px rgba(0,0,0,0.5)",
        }}>
          {isIllegal
            ? "Not in Our Scope \uD83C\uDFE5"
            : "Oops! That\u2019s not in our formulary \uD83D\uDE05"}
        </h2>

        {/* Search query display */}
        <p style={{
          marginTop: 12,
          fontSize: "clamp(13px, 1.4vw, 18px)",
          fontWeight: 400,
          color: "rgba(160, 180, 220, 0.75)",
          textAlign: "center",
          padding: "0 24px",
          textShadow: "0 1px 10px rgba(0,0,0,0.4)",
        }}>
          {query
            ? "You searched: \u201c" + query + "\u201d"
            : ""}
        </p>

        {/* Custom message if provided */}
        {message && (
          <p style={{
            marginTop: 8,
            fontSize: "clamp(12px, 1.2vw, 16px)",
            fontWeight: 400,
            color: "rgba(160, 180, 220, 0.6)",
            textAlign: "center",
            padding: "0 24px",
          }}>
            {message}
          </p>
        )}
      </div>

      {/* ── Glass "Return to Search" button ───────────────────── */}
      <button
        onClick={() => window.history.back()}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "rgba(255,255,255,0.18)";
          e.currentTarget.style.transform = "translateX(-50%) scale(1.05)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "rgba(255,255,255,0.1)";
          e.currentTarget.style.transform = "translateX(-50%) scale(1)";
        }}
        style={{
          position: "fixed",
          bottom: 40,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 10,
          pointerEvents: "auto",
          background: "rgba(255,255,255,0.1)",
          backdropFilter: "blur(20px) saturate(180%)",
          WebkitBackdropFilter: "blur(20px) saturate(180%)",
          border: "1px solid rgba(255,255,255,0.25)",
          borderRadius: 50,
          padding: "14px 32px",
          color: "#ffffff",
          fontSize: 16,
          fontWeight: 600,
          cursor: "pointer",
          boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.2)",
          transition: "all 0.2s ease",
          fontFamily: "'Inter', system-ui, sans-serif",
        }}
      >
        {"\u2190 Return to Search"}
      </button>
    </div>
  );
}
