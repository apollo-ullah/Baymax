"use client";

import { useEffect, useRef } from "react";
import {
  defaultNetwork,
  meshPalette,
  statusTint,
  type MeshNetwork,
} from "@/lib/mesh";
import { useCrisisMode } from "@/components/CrisisMode";

type RGB = readonly [number, number, number];

const mix = (a: RGB, b: RGB, t: number): RGB => [
  a[0] + (b[0] - a[0]) * t,
  a[1] + (b[1] - a[1]) * t,
  a[2] + (b[2] - a[2]) * t,
];
const rgba = (c: RGB, a: number) =>
  `rgba(${c[0] | 0}, ${c[1] | 0}, ${c[2] | 0}, ${a})`;

interface RuntimeNode {
  x: number; // px
  y: number;
  baseR: number;
  tint: RGB;
  load: number;
  phase: number;
  flash: number;
}

interface RuntimeEdge {
  a: number; // node index
  b: number;
}

interface ActiveTransfer {
  edge: number;
  progress: number;
  speed: number;
}

export interface NetworkMeshProps {
  network?: MeshNetwork;
  className?: string;
  /** How busy the network feels, 0..1 (transfer spawn rate). */
  density?: number;
  /** Subtle pointer parallax on the constellation. */
  interactive?: boolean;
}

export function NetworkMesh({
  network = defaultNetwork,
  className,
  density = 1,
  interactive = true,
}: NetworkMeshProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { isCrisis } = useCrisisMode();
  const crisisRef = useRef(isCrisis);

  useEffect(() => {
    crisisRef.current = isCrisis;
  }, [isCrisis]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // graceful: nothing renders, layout unaffected

    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const coarse = window.matchMedia("(pointer: coarse)").matches;
    const allowParallax = interactive && !reduceMotion && !coarse;

    let width = 0;
    let height = 0;
    let dpr = 1;
    const nodes: RuntimeNode[] = [];
    const edges: RuntimeEdge[] = network.edges
      .map((e) => ({
        a: network.nodes.findIndex((n) => n.id === e.from),
        b: network.nodes.findIndex((n) => n.id === e.to),
      }))
      .filter((e) => e.a >= 0 && e.b >= 0);

    let transfers: ActiveTransfer[] = [];
    let warmth = crisisRef.current ? 1 : 0;
    let spawnClock = 0;
    const pointer = { x: 0.5, y: 0.5, active: false };
    const parallax = { x: 0, y: 0 };

    const layout = () => {
      const rect = wrap.getBoundingClientRect();
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const minDim = Math.min(width, height);
      const pad = minDim * 0.08;
      const w = width - pad * 2;
      const h = height - pad * 2;
      nodes.length = 0;
      network.nodes.forEach((n, i) => {
        nodes.push({
          x: pad + n.x * w,
          y: pad + n.y * h,
          baseR: Math.min(Math.max(minDim * 0.011, 3.2), 7) *
            (0.68 + n.load * 0.7),
          tint: statusTint[n.status],
          load: n.load,
          phase: (i * 2.399963) % (Math.PI * 2),
          flash: 0,
        });
      });
    };

    const drawEdge = (e: RuntimeEdge, accent: RGB, baseAlpha: number) => {
      const a = nodes[e.a];
      const b = nodes[e.b];
      if (!a || !b) return;
      ctx.strokeStyle = rgba(accent, baseAlpha);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    };

    const drawTransfer = (t: ActiveTransfer, flow: RGB) => {
      const e = edges[t.edge];
      if (!e) return;
      const a = nodes[e.a];
      const b = nodes[e.b];
      if (!a || !b) return;
      const p = t.progress;
      const tail = Math.max(0, p - 0.22);
      const hx = a.x + (b.x - a.x) * p;
      const hy = a.y + (b.y - a.y) * p;
      const tx = a.x + (b.x - a.x) * tail;
      const ty = a.y + (b.y - a.y) * tail;

      // comet tail
      const grad = ctx.createLinearGradient(tx, ty, hx, hy);
      grad.addColorStop(0, rgba(flow, 0));
      grad.addColorStop(1, rgba(flow, 0.85));
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.8;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(hx, hy);
      ctx.stroke();

      // head glow
      ctx.fillStyle = rgba(flow, 0.95);
      ctx.beginPath();
      ctx.arc(hx, hy, 2.6, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawNode = (n: RuntimeNode, time: number, accent: RGB) => {
      const breathe = 1 + Math.sin(time * 0.0016 + n.phase) * 0.12;
      const tint = mix(n.tint, accent, warmth * 0.55);
      const r = n.baseR * breathe;
      const glowR = r * (5.5 + n.flash * 4);

      // halo
      const halo = ctx.createRadialGradient(n.x, n.y, r * 0.5, n.x, n.y, glowR);
      halo.addColorStop(0, rgba(tint, 0.22 + n.flash * 0.4));
      halo.addColorStop(1, rgba(tint, 0));
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(n.x, n.y, glowR, 0, Math.PI * 2);
      ctx.fill();

      // capacity ring
      ctx.strokeStyle = rgba(tint, 0.5);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 4.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * n.load);
      ctx.stroke();

      // core
      ctx.fillStyle = rgba(tint, 0.95);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = rgba([255, 255, 255], 0.65 + n.flash * 0.35);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r * 0.4, 0, Math.PI * 2);
      ctx.fill();
    };

    const render = (time: number) => {
      const accent = mix(meshPalette.calm.edge, meshPalette.crisis.edge, warmth);
      const flow = mix(meshPalette.calm.flow, meshPalette.crisis.flow, warmth);

      ctx.clearRect(0, 0, width, height);
      ctx.save();
      ctx.translate(parallax.x, parallax.y);

      // resting edges
      for (const e of edges) drawEdge(e, accent, 0.13 + warmth * 0.05);

      // active transfers (additive glow)
      ctx.globalCompositeOperation = "lighter";
      for (const t of transfers) drawTransfer(t, flow);
      ctx.globalCompositeOperation = "source-over";

      // nodes
      for (const n of nodes) drawNode(n, time, accent);

      ctx.restore();
    };

    const spawn = () => {
      const maxConcurrent = Math.round(2 + warmth * 4);
      if (transfers.length >= maxConcurrent || edges.length === 0) return;
      const busy = new Set(transfers.map((t) => t.edge));
      const free: number[] = [];
      for (let i = 0; i < edges.length; i++) if (!busy.has(i)) free.push(i);
      if (free.length === 0) return;
      const edge = free[(Math.random() * free.length) | 0];
      transfers.push({
        edge,
        progress: 0,
        speed: 0.4 + warmth * 0.5 + Math.random() * 0.2,
      });
    };

    let last = performance.now();
    let raf = 0;

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden) {
        last = now;
        return;
      }
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      // ease warmth toward the live mode
      const target = crisisRef.current ? 1 : 0;
      warmth += (target - warmth) * Math.min(dt * 3, 1);

      // ease parallax toward pointer
      if (allowParallax) {
        const maxShift = Math.min(width, height) * 0.018;
        const tx = pointer.active ? (pointer.x - 0.5) * maxShift * 2 : 0;
        const ty = pointer.active ? (pointer.y - 0.5) * maxShift * 2 : 0;
        parallax.x += (tx - parallax.x) * Math.min(dt * 2.5, 1);
        parallax.y += (ty - parallax.y) * Math.min(dt * 2.5, 1);
      }

      // spawn transfers on a warmth-scaled cadence
      spawnClock -= dt;
      if (spawnClock <= 0) {
        spawn();
        spawnClock = (1.5 - warmth * 0.85) / Math.max(density, 0.2);
      }

      // advance transfers
      transfers = transfers.filter((t) => {
        t.progress += t.speed * dt;
        if (t.progress >= 1) {
          const e = edges[t.edge];
          if (e && nodes[e.b]) nodes[e.b].flash = 1;
          return false;
        }
        return true;
      });

      // decay flashes
      for (const n of nodes) n.flash *= Math.pow(0.0001, dt);

      render(now);
    };

    const renderStatic = () => {
      // reduced-motion: a single composed frame with a few mid-edge pulses.
      warmth = crisisRef.current ? 1 : 0;
      transfers = [0, 3, 6].map((edge) => ({ edge, progress: 0.5, speed: 0 }));
      render(performance.now());
    };

    const onPointerMove = (e: PointerEvent) => {
      const rect = wrap.getBoundingClientRect();
      pointer.x = (e.clientX - rect.left) / rect.width;
      pointer.y = (e.clientY - rect.top) / rect.height;
      pointer.active = true;
    };
    const onPointerLeave = () => (pointer.active = false);

    const ro = new ResizeObserver(() => {
      layout();
      if (reduceMotion) renderStatic();
    });
    ro.observe(wrap);
    layout();

    if (reduceMotion) {
      renderStatic();
    } else {
      if (allowParallax) {
        wrap.addEventListener("pointermove", onPointerMove);
        wrap.addEventListener("pointerleave", onPointerLeave);
      }
      raf = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      wrap.removeEventListener("pointermove", onPointerMove);
      wrap.removeEventListener("pointerleave", onPointerLeave);
    };
  }, [network, density, interactive]);

  return (
    <div
      ref={wrapRef}
      className={className}
      role="img"
      aria-label="A live map of the hospital network — facilities as nodes, supply transfers traveling between them."
    >
      <canvas ref={canvasRef} className="block h-full w-full" />
    </div>
  );
}
