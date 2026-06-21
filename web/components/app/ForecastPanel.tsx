"use client";

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { getApi, type Forecast } from "@/lib/api";
import { ease, dur } from "@/lib/motion";
import { cn } from "@/lib/cn";

// Compact chart geometry (own viewBox so it scales cleanly).
const VB = { w: 360, h: 150 };
const PAD = { t: 14, r: 12, b: 22, l: 12 };
const PLOT = { x0: PAD.l, x1: VB.w - PAD.r, y0: PAD.t, y1: VB.h - PAD.b };
const Y_MAX = 480;

function MiniChart({ forecast }: { forecast: Forecast }) {
  const reduce = useReducedMotion();
  const n = forecast.points.length;
  const xAt = (i: number) => PLOT.x0 + (i / (n - 1)) * (PLOT.x1 - PLOT.x0);
  const yAt = (v: number) =>
    PLOT.y1 - Math.min(Math.max(v / Y_MAX, 0), 1) * (PLOT.y1 - PLOT.y0);

  const obs = forecast.points
    .map((p, i) => (p.observed != null ? `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.observed).toFixed(1)}` : null))
    .filter(Boolean)
    .join(" ");
  const fcIdx = forecast.points.findIndex((p) => p.forecast != null);
  const fc = forecast.points
    .map((p, i) => (p.forecast != null ? `${i === fcIdx ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.forecast).toFixed(1)}` : null))
    .filter(Boolean)
    .join(" ");
  const thrY = yAt(forecast.threshold);

  return (
    <div className="mode-shift text-accent">
      <svg
        viewBox={`0 0 ${VB.w} ${VB.h}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        className="block w-full"
        role="img"
        aria-label={`Forecast for ${forecast.item}: stock declines below the safety threshold — ${forecast.shortfallLabel}.`}
      >
        <line x1={PLOT.x0} x2={PLOT.x1} y1={thrY} y2={thrY} className="stroke-amber" strokeWidth={1.2} strokeDasharray="2 5" />
        <text x={PLOT.x1} y={thrY - 5} textAnchor="end" className="fill-amber [font-family:var(--font-mono)]" fontSize={9}>
          safety
        </text>
        <motion.path
          d={obs}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          initial={reduce ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: dur.base, ease: ease.outSoft }}
        />
        <motion.path
          d={fc}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeOpacity={0.75}
          strokeDasharray="5 6"
          strokeLinecap="round"
          initial={reduce ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: dur.slow, ease: ease.outSoft, delay: 0.2 }}
        />
        {forecast.points.map((p, i) => (
          <text key={i} x={xAt(i)} y={VB.h - 7} textAnchor="middle" className="fill-ink-3 [font-family:var(--font-mono)]" fontSize={8}>
            {p.label}
          </text>
        ))}
      </svg>
    </div>
  );
}

export function ForecastPanel() {
  const reduce = useReducedMotion();
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [loading, setLoading] = useState(false);

  const ingest = async () => {
    setLoading(true);
    try {
      const f = await getApi().getForecast();
      setForecast(f);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mode-shift rounded-xl border border-hairline bg-canvas-raised p-5 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)] sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <span className="t-eyebrow text-ink-3">Forecast</span>
        <motion.button
          type="button"
          onClick={ingest}
          disabled={loading}
          whileHover={reduce || loading ? undefined : { scale: 1.03 }}
          whileTap={reduce || loading ? undefined : { scale: 0.97 }}
          transition={{ duration: dur.micro, ease: ease.press }}
          className="mode-shift inline-flex h-9 items-center gap-2 rounded-pill border border-hairline-strong bg-canvas px-4 text-[0.85rem] font-medium text-ink transition-colors hover:border-accent disabled:opacity-50"
        >
          {loading ? "Ingesting…" : forecast ? "Re-ingest" : "Ingest data"}
        </motion.button>
      </div>

      {!forecast && !loading && (
        <p className="t-body mt-4 text-ink-2">
          Pull in usage history and live camera counts to project demand and
          surface what to order before it runs short.
        </p>
      )}

      {loading && (
        <div className="mt-4 space-y-3" aria-hidden="true">
          <div className="h-[150px] animate-pulse rounded-lg bg-paper-sunken" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-paper-sunken" />
        </div>
      )}

      {forecast && !loading && (
        <motion.div
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: dur.base }}
          className="mt-4"
        >
          <div className="flex items-baseline justify-between">
            <span className="text-[0.95rem] font-medium text-ink">
              {forecast.item} · on hand
            </span>
            <span className="t-mono text-coral">{forecast.shortfallLabel}</span>
          </div>
          <div className="mt-2">
            <MiniChart forecast={forecast} />
          </div>

          <div className="mt-4 border-t border-hairline pt-4">
            <p className="t-eyebrow text-ink-3">Recommended orders</p>
            <ul className="mt-3 space-y-2.5">
              {forecast.recommendations.map((r) => (
                <li
                  key={r.id}
                  className="flex items-start justify-between gap-3 rounded-lg border border-hairline bg-canvas px-3.5 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="text-[0.92rem] text-ink">
                      <span className="font-medium">{r.qty}</span> {r.item}
                    </p>
                    <p className="t-mono mt-0.5 text-ink-3">{r.rationale}</p>
                  </div>
                  <span className="mode-shift shrink-0 rounded-pill bg-accent-soft px-2.5 py-1 text-[0.72rem] font-medium text-accent-deep">
                    by {r.by}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </motion.div>
      )}
    </div>
  );
}
