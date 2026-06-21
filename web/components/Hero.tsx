import type { CSSProperties } from "react";
import { brand } from "@/lib/brand";
import { Button } from "@/components/ui/Button";
import { Magnetic } from "@/components/ui/Magnetic";
import { NetworkMesh } from "@/components/NetworkMesh";
import { StatusPill } from "@/components/StatusPill";
import { CrisisToggle } from "@/components/CrisisToggle";

/** Inline stagger delay for the CSS entrance. */
const d = (seconds: number): CSSProperties =>
  ({ "--rise-delay": `${seconds}s` }) as CSSProperties;

export function Hero() {
  return (
    <section className="relative overflow-hidden">
      {/* ambient accent wash that warms with the mode */}
      <div className="ambient-glow pointer-events-none absolute inset-0 -z-10" />

      <div className="mx-auto grid max-w-[1240px] items-center gap-12 px-5 pb-20 pt-14 sm:px-8 lg:grid-cols-12 lg:gap-8 lg:pb-28 lg:pt-20">
        {/* ── Thesis ── */}
        <div className="lg:col-span-6 lg:pr-6">
          <div className="rise-in" style={d(0)}>
            <StatusPill />
          </div>

          <h1 className="t-hero rise-in mt-7 text-ink" style={d(0.06)}>
            Supply that moves{" "}
            <span className="mode-shift text-accent">
              before the shortage does.
            </span>
          </h1>

          <p className="t-lead rise-in mt-6 max-w-xl" style={d(0.12)}>
            {brand.name} sees the shortfall coming across your hospital network,
            negotiates a transfer between facilities, and settles it —
            autonomously, before a shelf ever runs empty.
          </p>

          <p
            className="t-mono rise-in mt-6 flex flex-wrap gap-x-3 gap-y-1 text-ink-3"
            style={d(0.18)}
            aria-hidden="true"
          >
            <span>8 facilities</span>
            <span aria-hidden>·</span>
            <span>11 supply routes</span>
            <span aria-hidden>·</span>
            <span>settles in &lt;2s</span>
          </p>

          <div
            className="rise-in mt-9 flex flex-wrap items-center gap-3.5"
            style={d(0.24)}
          >
            <Magnetic>
              <Button href="/app" size="lg" variant="primary">
                Open the dashboard
              </Button>
            </Magnetic>
            <Button href="#how" size="lg" variant="ghost">
              See how it works
            </Button>
          </div>

          <div className="rise-in mt-9" style={d(0.3)}>
            <CrisisToggle />
            <p className="t-mono mt-2.5 text-ink-3">
              Flip to crisis — watch the whole room warm to urgency.
            </p>
          </div>
        </div>

        {/* ── Live monitor ── */}
        <div className="rise-in lg:col-span-6" style={d(0.16)}>
          <div className="mode-shift relative overflow-hidden rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_40px_80px_-48px_rgba(20,25,28,0.35)]">
            {/* monitor header */}
            <div className="absolute inset-x-0 top-0 z-10 flex items-center justify-between px-5 py-4">
              <span className="t-eyebrow text-ink-3">Live network</span>
              <span className="t-mono text-ink-3">8 facilities synced</span>
            </div>
            {/* soft top fade for depth */}
            <div className="pointer-events-none absolute inset-x-0 top-0 z-[5] h-24 bg-gradient-to-b from-canvas-raised/90 to-transparent" />

            <NetworkMesh className="h-[58vh] min-h-[360px] w-full lg:h-[72vh] lg:max-h-[640px]" />
          </div>
        </div>
      </div>
    </section>
  );
}
