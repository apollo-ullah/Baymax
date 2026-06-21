"use client";

import { motion, useReducedMotion } from "motion/react";
import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { Button } from "@/components/ui/Button";
import { Magnetic } from "@/components/ui/Magnetic";
import { brand } from "@/lib/brand";
import { ease, dur, inView } from "@/lib/motion";

/* ----------------------------------------------------------------------------
   Handcrafted forecast chart geometry.

   X = days, Mon of this week through Mon of next week (8 samples).
   Y = on-hand units of IV fluids.
   Observed (solid) runs Mon→Tue ("today"); the forecast (dashed) continues to
   next Mon and crosses the safety threshold on Thursday.

   Everything is plotted in a fixed 720×360 viewBox so the SVG can scale fluidly
   while the coordinate math stays readable. Stroke/fill colors come from the
   `text-*` token on a parent via currentColor, so the whole chart shifts
   teal→coral with crisis mode.
---------------------------------------------------------------------------- */

const VB = { w: 720, h: 360 };
const PAD = { top: 28, right: 24, bottom: 44, left: 52 };
const PLOT = {
  x0: PAD.left,
  x1: VB.w - PAD.right,
  y0: PAD.top,
  y1: VB.h - PAD.bottom,
};

const Y_MAX = 600; // top of the unit scale
const SAFETY = 180; // safety threshold, units of IV fluids

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"] as const;

// On-hand units across the window. Index 0..1 observed, 2..7 forecast.
// Tuesday is "today" — the seam between solid and dashed.
const UNITS = [520, 470, 410, 300, 200, 120, 70, 40];
const TODAY_INDEX = 1;

function xAt(i: number) {
  return PLOT.x0 + (i / (DAYS.length - 1)) * (PLOT.x1 - PLOT.x0);
}
function yAt(units: number) {
  const t = Math.min(Math.max(units / Y_MAX, 0), 1);
  return PLOT.y1 - t * (PLOT.y1 - PLOT.y0);
}

// Smooth-ish polyline path from an index range.
function linePath(from: number, to: number) {
  let d = "";
  for (let i = from; i <= to; i++) {
    d += `${i === from ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(UNITS[i]).toFixed(1)} `;
  }
  return d.trim();
}

// Closed area under the forecast segment, down to the baseline.
function areaPath(from: number, to: number) {
  let d = `M ${xAt(from).toFixed(1)} ${PLOT.y1.toFixed(1)} `;
  for (let i = from; i <= to; i++) {
    d += `L ${xAt(i).toFixed(1)} ${yAt(UNITS[i]).toFixed(1)} `;
  }
  d += `L ${xAt(to).toFixed(1)} ${PLOT.y1.toFixed(1)} Z`;
  return d;
}

// Linear interpolation to find where the forecast crosses the safety line.
function crossing() {
  for (let i = TODAY_INDEX; i < UNITS.length - 1; i++) {
    const a = UNITS[i];
    const b = UNITS[i + 1];
    if ((a - SAFETY) * (b - SAFETY) <= 0 && a !== b) {
      const f = (a - SAFETY) / (a - b);
      const x = xAt(i) + f * (xAt(i + 1) - xAt(i));
      const dayIdx = Math.round(i + f);
      return { x, day: DAYS[dayIdx] ?? DAYS[i + 1] };
    }
  }
  return { x: xAt(DAYS.length - 1), day: DAYS[DAYS.length - 1] };
}

const Y_TICKS = [0, 200, 400, 600];

export function ForecastSection() {
  const reduce = useReducedMotion();

  const todayX = xAt(TODAY_INDEX);
  const cross = crossing();
  const crossY = yAt(SAFETY);

  const drawTransition = { duration: dur.slow, ease: ease.outSoft, delay: 0.25 };

  return (
    <Section id="forecast">
      <Reveal>
        <Eyebrow index="04">Forecast</Eyebrow>
      </Reveal>

      <div className="mt-8 grid gap-10 lg:mt-12 lg:grid-cols-12 lg:items-center lg:gap-14">
        {/* ── Copy column ── */}
        <div className="lg:col-span-5">
          <Reveal>
            <h2 className="t-display text-ink">
              It already knows what next week needs.
            </h2>
          </Reveal>

          <Reveal delay={0.06}>
            <p className="t-lead mt-6 max-w-xl">
              {brand.name} reads real usage and live camera counts off the shelf,
              then projects each item forward. When the line is heading for the
              safety threshold, it surfaces the order days early — while there is
              still time to move calmly.
            </p>
          </Reveal>

          <Reveal delay={0.12}>
            <ul className="mt-8 space-y-4">
              {[
                {
                  label: "Ingests",
                  body: "Dispensing history and per-shelf camera counts, refreshed continuously.",
                },
                {
                  label: "Projects",
                  body: "On-hand units forward across the week, item by item.",
                },
                {
                  label: "Flags",
                  body: "The exact day each item is set to cross its safety threshold.",
                },
              ].map((row) => (
                <li key={row.label} className="flex gap-4">
                  <span
                    className="mode-shift mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent"
                    aria-hidden="true"
                  />
                  <p className="t-body">
                    <span className="mode-shift font-medium text-ink">
                      {row.label}.
                    </span>{" "}
                    {row.body}
                  </p>
                </li>
              ))}
            </ul>
          </Reveal>

          {/* Recommendation chip */}
          <Reveal delay={0.18}>
            <figure
              className="mode-shift mt-9 rounded-lg border border-accent bg-accent-soft p-4 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)]"
              aria-label="Forecast recommendation"
            >
              <figcaption className="t-mono mode-shift flex items-center gap-2 text-accent-deep">
                <span
                  className="mode-shift h-1.5 w-1.5 rounded-full bg-accent"
                  aria-hidden="true"
                />
                Recommended action
              </figcaption>
              <p className="t-mono mt-2.5 text-ink">
                Order 200 units of IV fluids by Thursday.
              </p>
              <p className="t-mono mt-1 text-ink-3">
                One human approval in chat releases the order.
              </p>
            </figure>
          </Reveal>

          <Reveal delay={0.24}>
            <div className="mt-8">
              <Magnetic>
                <Button href="/app" variant="ghost" size="md">
                  See a live forecast
                </Button>
              </Magnetic>
            </div>
          </Reveal>
        </div>

        {/* ── Chart card ── */}
        <div className="lg:col-span-7">
          <Reveal delay={0.1} y={28}>
            <figure className="mode-shift rounded-xl border border-hairline bg-canvas-raised p-5 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)] sm:p-7">
              <figcaption className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="t-title text-ink">IV fluids · on hand</span>
                <span className="t-mono text-ink-3">units · this week → next</span>
              </figcaption>

              {/* The accent-driven chart. text-accent sets currentColor for the
                  forecast line/area; gridlines + axes use token classes. */}
              <div className="mode-shift text-accent">
                <svg
                  viewBox={`0 0 ${VB.w} ${VB.h}`}
                  preserveAspectRatio="xMidYMid meet"
                  width="100%"
                  className="block h-auto w-full"
                  role="img"
                  aria-label={`Forecast of IV fluids on hand. Stock is observed declining through today, Tuesday, and is projected to fall below the safety threshold of ${SAFETY} units on ${cross.day}.`}
                >
                  {/* Horizontal gridlines + Y ticks */}
                  {Y_TICKS.map((t) => {
                    const y = yAt(t);
                    return (
                      <g key={t}>
                        <line
                          x1={PLOT.x0}
                          x2={PLOT.x1}
                          y1={y}
                          y2={y}
                          className="stroke-hairline"
                          strokeWidth={1}
                        />
                        <text
                          x={PLOT.x0 - 12}
                          y={y + 4}
                          textAnchor="end"
                          className="fill-ink-3 [font-family:var(--font-mono)]"
                          fontSize={12}
                        >
                          {t}
                        </text>
                      </g>
                    );
                  })}

                  {/* X axis day labels */}
                  {DAYS.map((d, i) => (
                    <text
                      key={`${d}-${i}`}
                      x={xAt(i)}
                      y={PLOT.y1 + 26}
                      textAnchor="middle"
                      className="fill-ink-3 [font-family:var(--font-mono)]"
                      fontSize={12}
                    >
                      {d}
                    </text>
                  ))}

                  {/* Safety threshold line */}
                  <line
                    x1={PLOT.x0}
                    x2={PLOT.x1}
                    y1={crossY}
                    y2={crossY}
                    className="stroke-amber"
                    strokeWidth={1.5}
                    strokeDasharray="2 6"
                    strokeLinecap="round"
                  />
                  <text
                    x={PLOT.x1}
                    y={crossY - 9}
                    textAnchor="end"
                    className="fill-amber [font-family:var(--font-mono)]"
                    fontSize={12}
                  >
                    safety · {SAFETY}
                  </text>

                  {/* Shaded area under the forecast segment */}
                  <motion.path
                    d={areaPath(TODAY_INDEX, DAYS.length - 1)}
                    fill="currentColor"
                    fillOpacity={0.1}
                    initial={reduce ? false : { opacity: 0 }}
                    whileInView={reduce ? undefined : { opacity: 1 }}
                    viewport={inView}
                    transition={{ duration: dur.base, ease: ease.outSoft, delay: 0.5 }}
                  />

                  {/* Observed line — solid, up to today */}
                  <motion.path
                    d={linePath(0, TODAY_INDEX)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    initial={reduce ? false : { pathLength: 0 }}
                    whileInView={reduce ? undefined : { pathLength: 1 }}
                    viewport={inView}
                    transition={{ duration: dur.base, ease: ease.outSoft, delay: 0.15 }}
                  />

                  {/* Forecast line — dashed, from today onward */}
                  <motion.path
                    d={linePath(TODAY_INDEX, DAYS.length - 1)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeDasharray="6 7"
                    strokeOpacity={0.78}
                    initial={reduce ? false : { pathLength: 0 }}
                    whileInView={reduce ? undefined : { pathLength: 1 }}
                    viewport={inView}
                    transition={drawTransition}
                  />

                  {/* "Today" vertical divider */}
                  <line
                    x1={todayX}
                    x2={todayX}
                    y1={PLOT.y0 - 6}
                    y2={PLOT.y1}
                    className="stroke-hairline-strong"
                    strokeWidth={1.5}
                    strokeDasharray="3 5"
                  />
                  <text
                    x={todayX}
                    y={PLOT.y0 - 12}
                    textAnchor="middle"
                    className="fill-ink-3 [font-family:var(--font-mono)]"
                    fontSize={12}
                  >
                    today
                  </text>

                  {/* Observed "now" dot */}
                  <circle
                    cx={todayX}
                    cy={yAt(UNITS[TODAY_INDEX])}
                    r={4.5}
                    fill="currentColor"
                  />

                  {/* Projected shortfall marker where forecast crosses safety */}
                  <motion.g
                    initial={reduce ? false : { opacity: 0, scale: 0.6 }}
                    whileInView={reduce ? undefined : { opacity: 1, scale: 1 }}
                    viewport={inView}
                    transition={{ duration: dur.base, ease: ease.press, delay: 1.05 }}
                    style={{ transformOrigin: `${cross.x}px ${crossY}px` }}
                  >
                    <circle
                      cx={cross.x}
                      cy={crossY}
                      r={9}
                      fill="currentColor"
                      fillOpacity={0.16}
                    />
                    <circle
                      cx={cross.x}
                      cy={crossY}
                      r={4.5}
                      fill="currentColor"
                      stroke="var(--canvas-raised)"
                      strokeWidth={2}
                    />
                    <text
                      x={cross.x}
                      y={crossY + 30}
                      textAnchor="middle"
                      className="fill-ink [font-family:var(--font-mono)]"
                      fontSize={12.5}
                      fontWeight={500}
                    >
                      projected shortfall — {cross.day}
                    </text>
                  </motion.g>
                </svg>
              </div>

              {/* Legend */}
              <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-hairline pt-4">
                <span className="t-mono mode-shift flex items-center gap-2 text-ink-2">
                  <span
                    className="mode-shift h-0.5 w-6 rounded-full bg-accent"
                    aria-hidden="true"
                  />
                  observed
                </span>
                <span className="t-mono mode-shift flex items-center gap-2 text-ink-2">
                  <span
                    className="mode-shift h-0.5 w-6 rounded-full bg-accent/60 [background-image:repeating-linear-gradient(90deg,currentColor_0_5px,transparent_5px_10px)]"
                    aria-hidden="true"
                  />
                  forecast
                </span>
                <span className="t-mono flex items-center gap-2 text-ink-2">
                  <span className="h-0.5 w-6 rounded-full bg-amber" aria-hidden="true" />
                  safety threshold
                </span>
              </div>
            </figure>
          </Reveal>
        </div>
      </div>
    </Section>
  );
}
