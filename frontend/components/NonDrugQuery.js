/**
 * NonDrugQuery — 3D animated pill capsule shown for non-drug searches.
 *
 * Uses Three.js (r128 via CDN) to render a glossy two-tone capsule with
 * sparkles, glow, and hover effects. The pill is built from two hemispheres
 * + two half-cylinders (no CapsuleGeometry needed).
 *
 * Props:
 *   query   — the user's original search string (displayed in subtext)
 *   message — optional custom rejection message
 *   isIllegal — if true, shows SAMHSA helpline instead of suggestions
 */

import { useEffect, useRef } from "react";

export default function NonDrugQuery({ query, message, isIllegal = false }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);

  useEffect(() => {
    // Dynamically import Three.js from CDN to avoid SSR issues
    let cancelled = false;

    async function init() {
      // Use the Three.js already in node_modules, or fall back to CDN
      let THREE;
      try {
        THREE = await import("three");
      } catch {
        // If import fails (shouldn't in Next.js), bail out gracefully
        console.warn("Three.js not available");
        return;
      }

      if (cancelled || !mountRef.current) return;

      const container = mountRef.current;
      const width = container.clientWidth;
      const height = container.clientHeight;

      // ── Scene ────────────────────────────────────────────────────────
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x060c1a);

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

      const capsuleRadius = 0.7;
      const capsuleLength = 1.6;

      // Blue material (left half)
      const blueMat = new THREE.MeshPhysicalMaterial({
        color: 0x1a3fd4,
        roughness: 0.08,
        metalness: 0.1,
        transmission: 0.15,
        thickness: 0.5,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
        envMapIntensity: 1.5,
      });

      // White material (right half)
      const whiteMat = new THREE.MeshPhysicalMaterial({
        color: 0xf0f4ff,
        roughness: 0.05,
        metalness: 0.05,
        transmission: 0.2,
        thickness: 0.5,
        clearcoat: 1.0,
        clearcoatRoughness: 0.03,
        envMapIntensity: 1.5,
      });

      // Blue hemisphere (left cap)
      const blueSphereGeo = new THREE.SphereGeometry(
        capsuleRadius, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2
      );
      const blueHemi = new THREE.Mesh(blueSphereGeo, blueMat);
      blueHemi.rotation.z = -Math.PI / 2;
      blueHemi.position.x = -capsuleLength / 2;
      blueHemi.castShadow = true;
      pillGroup.add(blueHemi);

      // Blue cylinder (left barrel)
      const blueCylGeo = new THREE.CylinderGeometry(
        capsuleRadius, capsuleRadius, capsuleLength / 2, 64, 1, true
      );
      const blueCyl = new THREE.Mesh(blueCylGeo, blueMat);
      blueCyl.rotation.z = Math.PI / 2;
      blueCyl.position.x = -capsuleLength / 4;
      blueCyl.castShadow = true;
      pillGroup.add(blueCyl);

      // White hemisphere (right cap)
      const whiteSphereGeo = new THREE.SphereGeometry(
        capsuleRadius, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2
      );
      const whiteHemi = new THREE.Mesh(whiteSphereGeo, whiteMat);
      whiteHemi.rotation.z = -Math.PI / 2;
      whiteHemi.position.x = capsuleLength / 2;
      whiteHemi.castShadow = true;
      pillGroup.add(whiteHemi);

      // White cylinder (right barrel)
      const whiteCylGeo = new THREE.CylinderGeometry(
        capsuleRadius, capsuleRadius, capsuleLength / 2, 64, 1, true
      );
      const whiteCyl = new THREE.Mesh(whiteCylGeo, whiteMat);
      whiteCyl.rotation.z = Math.PI / 2;
      whiteCyl.position.x = capsuleLength / 4;
      whiteCyl.castShadow = true;
      pillGroup.add(whiteCyl);

      // Seam ring between halves
      const seamGeo = new THREE.TorusGeometry(capsuleRadius + 0.002, 0.012, 16, 128);
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

      // Rx engraving on white half
      const rxCanvas = document.createElement("canvas");
      rxCanvas.width = 512;
      rxCanvas.height = 512;
      const rxCtx = rxCanvas.getContext("2d");
      rxCtx.clearRect(0, 0, 512, 512);
      rxCtx.font = "bold 148px Inter, sans-serif";
      rxCtx.textAlign = "center";
      rxCtx.textBaseline = "middle";
      rxCtx.fillStyle = "rgba(26, 63, 212, 0.55)";
      rxCtx.fillText("Rx", 256, 256);
      const rxTex = new THREE.CanvasTexture(rxCanvas);
      const whiteMatEngrave = whiteMat.clone();
      whiteMatEngrave.map = rxTex;
      whiteHemi.material = whiteMatEngrave;
      whiteCyl.material = whiteMatEngrave;

      // Pill tilt
      pillGroup.rotation.z = 0.3;

      // ── Grid Background ──────────────────────────────────────────────
      const gridCanvas = document.createElement("canvas");
      gridCanvas.width = 512;
      gridCanvas.height = 512;
      const gCtx = gridCanvas.getContext("2d");
      gCtx.fillStyle = "#060c1a";
      gCtx.fillRect(0, 0, 512, 512);
      gCtx.strokeStyle = "rgba(30, 60, 140, 0.22)";
      gCtx.lineWidth = 1;
      const gridSize = 32;
      for (let x = 0; x <= 512; x += gridSize) {
        gCtx.beginPath(); gCtx.moveTo(x, 0); gCtx.lineTo(x, 512); gCtx.stroke();
      }
      for (let y = 0; y <= 512; y += gridSize) {
        gCtx.beginPath(); gCtx.moveTo(0, y); gCtx.lineTo(512, y); gCtx.stroke();
      }
      const gridTex = new THREE.CanvasTexture(gridCanvas);
      gridTex.wrapS = THREE.RepeatWrapping;
      gridTex.wrapT = THREE.RepeatWrapping;
      gridTex.repeat.set(6, 4);
      const gridGeo = new THREE.PlaneGeometry(40, 28);
      const gridMat = new THREE.MeshBasicMaterial({
        map: gridTex, transparent: true, opacity: 1, depthWrite: false,
      });
      const gridPlane = new THREE.Mesh(gridGeo, gridMat);
      gridPlane.position.z = -5;
      scene.add(gridPlane);

      // ── Glow Sprites ─────────────────────────────────────────────────
      function createGlowSprite(color, size, opacity) {
        const c = document.createElement("canvas");
        c.width = 256; c.height = 256;
        const ctx = c.getContext("2d");
        const grad = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
        grad.addColorStop(0, `rgba(${color},${opacity})`);
        grad.addColorStop(0.4, `rgba(${color},${opacity * 0.4})`);
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

      const glowBlue = createGlowSprite("60,100,255", 6.5, 0.55);
      glowBlue.position.x = -0.5;
      scene.add(glowBlue);

      const glowWhite = createGlowSprite("200,210,255", 5.5, 0.35);
      glowWhite.position.x = 0.5;
      scene.add(glowWhite);

      const glowOuter = createGlowSprite("40,70,220", 9, 0.2);
      scene.add(glowOuter);

      // ── Sparkle Particles ────────────────────────────────────────────
      const sparkleCount = 80;
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

      // Sparkle star texture
      const sparkCanvas = document.createElement("canvas");
      sparkCanvas.width = 64; sparkCanvas.height = 64;
      const sCtx = sparkCanvas.getContext("2d");
      sCtx.fillStyle = "rgba(0,0,0,0)";
      sCtx.fillRect(0, 0, 64, 64);
      const drawStar = (cx, cy, r) => {
        sCtx.save(); sCtx.translate(cx, cy);
        for (let a = 0; a < 4; a++) {
          sCtx.save(); sCtx.rotate((a * Math.PI) / 2);
          const grad = sCtx.createLinearGradient(0, -r, 0, r);
          grad.addColorStop(0, "rgba(255,255,255,0)");
          grad.addColorStop(0.5, "rgba(255,255,255,1)");
          grad.addColorStop(1, "rgba(255,255,255,0)");
          sCtx.fillStyle = grad;
          sCtx.beginPath();
          sCtx.ellipse(0, 0, r * 0.12, r, 0, 0, Math.PI * 2);
          sCtx.fill(); sCtx.restore();
        }
        const cg = sCtx.createRadialGradient(0, 0, 0, 0, 0, r * 0.4);
        cg.addColorStop(0, "rgba(200,220,255,1)");
        cg.addColorStop(1, "rgba(200,220,255,0)");
        sCtx.fillStyle = cg;
        sCtx.beginPath();
        sCtx.arc(0, 0, r * 0.4, 0, Math.PI * 2);
        sCtx.fill(); sCtx.restore();
      };
      drawStar(32, 32, 28);
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
      scene.add(new THREE.AmbientLight(0x1a2a6c, 0.8));

      const keyLight = new THREE.DirectionalLight(0x6688ff, 3.5);
      keyLight.position.set(3, 3, 4);
      keyLight.castShadow = true;
      scene.add(keyLight);

      const fillLight = new THREE.DirectionalLight(0xffffff, 1.5);
      fillLight.position.set(-3, -1, 2);
      scene.add(fillLight);

      const rimLight = new THREE.DirectionalLight(0x3366ff, 2);
      rimLight.position.set(0, 2, -4);
      scene.add(rimLight);

      const bluePointLight = new THREE.PointLight(0x2244ff, 4, 8);
      bluePointLight.position.set(-2, 1, 2);
      scene.add(bluePointLight);

      // ── Hover Detection ──────────────────────────────────────────────
      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2();
      let isHovered = false;
      let hoverT = 0;
      const pillMeshes = [blueHemi, blueCyl, whiteHemi, whiteCyl];

      const onMouseMove = (e) => {
        const rect = container.getBoundingClientRect();
        mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(pillMeshes);
        isHovered = hits.length > 0;
        renderer.domElement.style.cursor = isHovered ? "pointer" : "default";
      };
      const onMouseLeave = () => { isHovered = false; };

      renderer.domElement.addEventListener("mousemove", onMouseMove);
      renderer.domElement.addEventListener("mouseleave", onMouseLeave);

      // ── Animation ────────────────────────────────────────────────────
      const clock = new THREE.Clock();
      const baseSpinSpeed = 0.4;
      const hoverSpinSpeed = 2.2;

      function animate() {
        const t = clock.getElapsedTime();
        hoverT += ((isHovered ? 1 : 0) - hoverT) * 0.06;
        const spinSpeed = baseSpinSpeed + (hoverSpinSpeed - baseSpinSpeed) * hoverT;

        pillGroup.rotation.y += spinSpeed * 0.016;
        pillGroup.position.y = Math.sin(t * 0.8) * 0.12;

        const pulse = 1 + Math.sin(t * 6) * 0.025 * hoverT;
        pillGroup.scale.setScalar(pulse);

        // Glow pulse
        const glowPulse = 1 + Math.sin(t * 3) * 0.15 * (0.5 + hoverT * 0.5);
        glowBlue.scale.setScalar(6.5 * glowPulse);
        glowWhite.scale.setScalar(5.5 * glowPulse);
        glowOuter.scale.setScalar(9 * (1 + Math.sin(t * 1.5) * 0.08));
        glowBlue.position.y = pillGroup.position.y;
        glowWhite.position.y = pillGroup.position.y;
        glowOuter.position.y = pillGroup.position.y;

        // Sparkle orbit
        const pos = sparkleGeo.attributes.position.array;
        for (let i = 0; i < sparkleCount; i++) {
          const orb = sparkleOrbits[i];
          orb.theta += orb.speed * 0.008 * (1 + hoverT * 1.5);
          pos[i * 3] = orb.r * Math.sin(orb.phi) * Math.cos(orb.theta);
          pos[i * 3 + 1] = orb.r * Math.sin(orb.phi) * Math.sin(orb.theta) + pillGroup.position.y * 0.3;
          pos[i * 3 + 2] = orb.r * Math.cos(orb.phi);
        }
        sparkleGeo.attributes.position.needsUpdate = true;
        sparkleMat.opacity = 0.7 + 0.3 * Math.sin(t * 1.8);

        seamMat.emissiveIntensity = 0.4 + hoverT * 0.8 + Math.sin(t * 5) * 0.2 * hoverT;
        blueMat.emissive = new THREE.Color(0x1a3fd4);
        blueMat.emissiveIntensity = 0.05 + hoverT * 0.12;
        bluePointLight.intensity = 3 + Math.sin(t * 2) * 1 + hoverT * 2;

        renderer.render(scene, camera);
      }

      renderer.setAnimationLoop(animate);

      // ── Resize ───────────────────────────────────────────────────────
      const onResize = () => {
        if (!container) return;
        const w = container.clientWidth;
        const h = container.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      };
      window.addEventListener("resize", onResize);

      // ── Cleanup ref ──────────────────────────────────────────────────
      cleanupRef.current = () => {
        renderer.setAnimationLoop(null);
        renderer.domElement.removeEventListener("mousemove", onMouseMove);
        renderer.domElement.removeEventListener("mouseleave", onMouseLeave);
        window.removeEventListener("resize", onResize);

        // Dispose geometries and materials
        scene.traverse((obj) => {
          if (obj.geometry) obj.geometry.dispose();
          if (obj.material) {
            if (Array.isArray(obj.material)) {
              obj.material.forEach((m) => m.dispose());
            } else {
              obj.material.dispose();
            }
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
  }, []); // mount once

  const displayQuery = query
    ? `You searched: "${query}"`
    : "That doesn't look like a medication.";

  return (
    <div style={{
      position: "relative",
      width: "100%",
      borderRadius: 16,
      overflow: "hidden",
      boxShadow: "0 4px 24px rgba(0,0,0,0.3)",
    }}>
      {/* Three.js canvas container */}
      <div
        ref={mountRef}
        style={{
          width: "100%",
          height: 420,
          background: "#060c1a",
        }}
      />

      {/* Text overlay */}
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
      }}>
        <h2 style={{
          marginTop: 32,
          fontSize: "clamp(18px, 2.8vw, 32px)",
          fontWeight: 800,
          color: "#ffffff",
          letterSpacing: "-0.01em",
          textAlign: "center",
          lineHeight: 1.15,
          padding: "0 24px",
        }}>
          {isIllegal ? "Not in Our Scope \uD83C\uDFE5" : "Oops! That's not in our formulary \uD83D\uDE05"}
        </h2>

        <p style={{
          marginTop: 10,
          fontSize: "clamp(12px, 1.3vw, 16px)",
          fontWeight: 400,
          color: "rgba(160, 180, 220, 0.75)",
          textAlign: "center",
          letterSpacing: "0.01em",
          padding: "0 24px",
        }}>
          {displayQuery}
        </p>
      </div>

      {/* Bottom info bar */}
      <div style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        right: 0,
        padding: "16px 24px",
        background: "linear-gradient(transparent, rgba(6,12,26,0.95))",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        pointerEvents: "auto",
      }}>
        {isIllegal ? (
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            background: "rgba(254,202,202,0.15)",
            borderRadius: 999,
            padding: "8px 20px",
            fontSize: 13,
            color: "#fca5a5",
            fontWeight: 500,
            border: "1px solid rgba(252,165,165,0.2)",
          }}>
            <span>{"\uD83D\uDCDE"}</span>
            <span>SAMHSA Helpline: 1-800-662-4357 (free, 24/7)</span>
          </div>
        ) : (
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            background: "rgba(130,160,255,0.12)",
            borderRadius: 999,
            padding: "8px 20px",
            fontSize: 13,
            color: "rgba(170,200,255,0.85)",
            fontWeight: 500,
            border: "1px solid rgba(130,160,255,0.2)",
          }}>
            <span>{"\u2728"}</span>
            <span>Try: &quot;lisinopril side effects&quot; or &quot;metformin dosage&quot;</span>
          </div>
        )}

        <span style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.38em",
          textTransform: "uppercase",
          color: "rgba(130, 160, 255, 0.4)",
        }}>
          RxBuddy
        </span>
      </div>
    </div>
  );
}
