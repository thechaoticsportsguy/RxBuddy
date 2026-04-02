/**
 * NonDrugQuery — Rainbow chromatic 3D spinning pill for non-drug searches.
 *
 * Uses Three.js to render a glossy pharmaceutical capsule with animated
 * rainbow HSL colors, sparkle particles, glow aura, and hover effects.
 * The pill is built from two hemispheres + two half-cylinders with an
 * "RxBuddy" engraving via canvas texture.
 *
 * Props:
 *   query     — the user's original search string
 *   isIllegal — if true, shows SAMHSA helpline instead of drug suggestions
 */

import { useEffect, useRef } from "react";

export default function NonDrugQuery({ query, isIllegal = false }) {
  const mountRef = useRef(null);
  const cleanupRef = useRef(null);

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
      const halfLen = capsuleLength / 2;
      const quarterLen = capsuleLength / 4;

      // Left-half material (will animate rainbow)
      const leftMat = new THREE.MeshPhysicalMaterial({
        color: 0x1a3fd4,
        roughness: 0.08,
        metalness: 0.1,
        transmission: 0.15,
        thickness: 0.5,
        clearcoat: 1.0,
        clearcoatRoughness: 0.05,
        envMapIntensity: 1.5,
      });

      // Right-half material (will animate complementary rainbow)
      const rightMat = new THREE.MeshPhysicalMaterial({
        color: 0xf0f4ff,
        roughness: 0.05,
        metalness: 0.05,
        transmission: 0.2,
        thickness: 0.5,
        clearcoat: 1.0,
        clearcoatRoughness: 0.03,
        envMapIntensity: 1.5,
      });

      // ── Left hemisphere (cap) ────────────────────────────────────────
      const leftHemiGeo = new THREE.SphereGeometry(
        capsuleRadius, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2
      );
      const leftHemi = new THREE.Mesh(leftHemiGeo, leftMat);
      leftHemi.rotation.z = -Math.PI / 2;
      leftHemi.position.x = -halfLen;
      leftHemi.castShadow = true;
      pillGroup.add(leftHemi);

      // ── Left cylinder (barrel) ───────────────────────────────────────
      const leftCylGeo = new THREE.CylinderGeometry(
        capsuleRadius, capsuleRadius, capsuleLength / 2, 64, 1, true
      );
      const leftCyl = new THREE.Mesh(leftCylGeo, leftMat);
      leftCyl.rotation.z = Math.PI / 2;
      leftCyl.position.x = -quarterLen;
      leftCyl.castShadow = true;
      pillGroup.add(leftCyl);

      // ── Right hemisphere (cap) ───────────────────────────────────────
      const rightHemiGeo = new THREE.SphereGeometry(
        capsuleRadius, 64, 32, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2
      );
      const rightHemi = new THREE.Mesh(rightHemiGeo, rightMat);
      rightHemi.rotation.z = -Math.PI / 2;
      rightHemi.position.x = halfLen;
      rightHemi.castShadow = true;
      pillGroup.add(rightHemi);

      // ── Right cylinder (barrel) ──────────────────────────────────────
      const rightCylGeo = new THREE.CylinderGeometry(
        capsuleRadius, capsuleRadius, capsuleLength / 2, 64, 1, true
      );
      const rightCyl = new THREE.Mesh(rightCylGeo, rightMat);
      rightCyl.rotation.z = Math.PI / 2;
      rightCyl.position.x = quarterLen;
      rightCyl.castShadow = true;
      pillGroup.add(rightCyl);

      // ── Seam ring between halves ─────────────────────────────────────
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

      // ── "RxBuddy" engraving on right half ────────────────────────────
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
      rightHemi.material = rightMatEngrave;
      rightCyl.material = rightMatEngrave;

      // Tilt the pill
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
      scene.add(new THREE.AmbientLight(0x1a2a6c, 0.8));

      const keyLight = new THREE.DirectionalLight(0xffffff, 3.5);
      keyLight.position.set(3, 3, 4);
      keyLight.castShadow = true;
      scene.add(keyLight);

      const fillLight = new THREE.DirectionalLight(0xffffff, 1.5);
      fillLight.position.set(-3, -1, 2);
      scene.add(fillLight);

      const rimLight = new THREE.DirectionalLight(0x3366ff, 2);
      rimLight.position.set(0, 2, -4);
      scene.add(rimLight);

      const pointLight = new THREE.PointLight(0x2244ff, 4, 8);
      pointLight.position.set(-2, 1, 2);
      scene.add(pointLight);

      // ── Hover Detection ──────────────────────────────────────────────
      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2();
      let isHovered = false;
      let hoverT = 0;
      const pillMeshes = [leftHemi, leftCyl, rightHemi, rightCyl];

      const onMouseMove = (e) => {
        const rect = container.getBoundingClientRect();
        mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        isHovered = raycaster.intersectObjects(pillMeshes).length > 0;
        renderer.domElement.style.cursor = isHovered ? "pointer" : "default";
      };
      const onMouseLeave = () => { isHovered = false; };
      renderer.domElement.addEventListener("mousemove", onMouseMove);
      renderer.domElement.addEventListener("mouseleave", onMouseLeave);

      // ── Animation Loop ───────────────────────────────────────────────
      const clock = new THREE.Clock();

      function animate() {
        const t = clock.getElapsedTime();

        // Smooth hover transition
        hoverT += ((isHovered ? 1 : 0) - hoverT) * 0.06;
        const spinSpeed = 0.4 + (2.2 - 0.4) * hoverT;

        // Spin + float
        pillGroup.rotation.y += spinSpeed * 0.016;
        pillGroup.position.y = Math.sin(t * 0.8) * 0.12;
        pillGroup.scale.setScalar(1 + Math.sin(t * 6) * 0.025 * hoverT);

        // ── Rainbow color cycling ────────────────────────────────────
        const hue = (t * 30) % 360;
        leftMat.color.setHSL(hue / 360, 0.85, 0.5);
        leftMat.emissive.setHSL(hue / 360, 0.9, 0.15);
        leftMat.emissiveIntensity = 0.15 + hoverT * 0.2;

        rightMat.color.setHSL(((hue + 180) % 360) / 360, 0.7, 0.7);
        rightMatEngrave.color.setHSL(((hue + 180) % 360) / 360, 0.7, 0.7);
        rightMatEngrave.emissive.setHSL(((hue + 180) % 360) / 360, 0.5, 0.1);
        rightMatEngrave.emissiveIntensity = 0.05 + hoverT * 0.1;

        // Seam follows the rainbow too
        seamMat.color.setHSL(((hue + 90) % 360) / 360, 1, 0.6);
        seamMat.emissive.setHSL(((hue + 90) % 360) / 360, 1, 0.3);
        seamMat.emissiveIntensity = 0.4 + hoverT * 0.8 + Math.sin(t * 5) * 0.2 * hoverT;

        // Key light color follows the primary hue
        keyLight.color.setHSL(hue / 360, 0.6, 0.7);

        // Sparkle color shifts
        sparkleMat.color.setHSL(((hue + 120) % 360) / 360, 0.8, 0.8);

        // Glow pulse
        const glowPulse = 1 + Math.sin(t * 3) * 0.15 * (0.5 + hoverT * 0.5);
        glowA.scale.setScalar(6.5 * glowPulse);
        glowB.scale.setScalar(5.5 * glowPulse);
        glowOuter.scale.setScalar(9 * (1 + Math.sin(t * 1.5) * 0.08));
        glowA.position.y = pillGroup.position.y;
        glowB.position.y = pillGroup.position.y;
        glowOuter.position.y = pillGroup.position.y;

        // Animate sparkle orbits
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

        // Point light pulses
        pointLight.intensity = 3 + Math.sin(t * 2) + hoverT * 2;

        renderer.render(scene, camera);
      }

      renderer.setAnimationLoop(animate);

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
        renderer.setAnimationLoop(null);
        renderer.domElement.removeEventListener("mousemove", onMouseMove);
        renderer.domElement.removeEventListener("mouseleave", onMouseLeave);
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
    <div
      ref={mountRef}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        background: "#060c1a",
        overflow: "hidden",
      }}
    >
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
        zIndex: 1,
      }}>
        <h2 style={{
          marginTop: 48,
          fontSize: "clamp(20px, 3vw, 40px)",
          fontWeight: 800,
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
            : "Try searching a real medication like \u2018lisinopril side effects\u2019"}
        </p>
      </div>

      {/* Bottom bar */}
      <div style={{
        position: "absolute",
        bottom: 0,
        left: 0,
        right: 0,
        padding: "20px 24px",
        background: "linear-gradient(transparent, rgba(6,12,26,0.95))",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
        zIndex: 1,
      }}>
        {isIllegal ? (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            background: "rgba(254,202,202,0.15)", borderRadius: 999,
            padding: "8px 20px", fontSize: 13, color: "#fca5a5",
            fontWeight: 500, border: "1px solid rgba(252,165,165,0.2)",
          }}>
            <span>{"\uD83D\uDCDE"}</span>
            <span>SAMHSA Helpline: 1-800-662-4357 (free, 24/7)</span>
          </div>
        ) : (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            background: "rgba(130,160,255,0.12)", borderRadius: 999,
            padding: "8px 20px", fontSize: 13,
            color: "rgba(170,200,255,0.85)", fontWeight: 500,
            border: "1px solid rgba(130,160,255,0.2)",
          }}>
            <span>{"\u2728"}</span>
            <span>Try: &quot;lisinopril side effects&quot; or &quot;metformin dosage&quot;</span>
          </div>
        )}

        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.38em",
          textTransform: "uppercase", color: "rgba(130,160,255,0.4)",
        }}>
          RxBuddy
        </span>
      </div>
    </div>
  );
}
