"use client";

import { motion, useReducedMotion } from "motion/react";
import { useCrisisMode, type Mode } from "@/components/CrisisMode";
import { ease, dur } from "@/lib/motion";
import { cn } from "@/lib/cn";

const OPTIONS: { value: Mode; label: string }[] = [
  { value: "calm", label: "Calm" },
  { value: "crisis", label: "Crisis" },
];

/**
 * Demo control — flips the environmental state so the palette shift is
 * witnessable on the landing page. In the real app this state is driven by
 * the backend crisis signal, not a toggle.
 */
export function CrisisToggle({ className }: { className?: string }) {
  const { mode, setMode } = useCrisisMode();
  const reduce = useReducedMotion();

  return (
    <div className={cn("inline-flex items-center gap-3", className)}>
      <span className="t-eyebrow text-ink-3">Demo</span>
      <div
        role="radiogroup"
        aria-label="Network state"
        className="mode-shift relative inline-flex rounded-pill border border-hairline-strong bg-canvas-raised p-1"
      >
        {OPTIONS.map((opt) => {
          const active = mode === opt.value;
          return (
            <button
              key={opt.value}
              role="radio"
              aria-checked={active}
              onClick={() => setMode(opt.value)}
              className={cn(
                "relative z-10 rounded-pill px-4 py-1.5 text-[0.82rem] font-medium transition-colors",
                active ? "text-on-accent" : "text-ink-2 hover:text-ink",
              )}
            >
              {active && (
                <motion.span
                  layoutId="crisis-toggle-pill"
                  className="absolute inset-0 -z-10 rounded-pill bg-accent"
                  transition={
                    reduce
                      ? { duration: 0 }
                      : { duration: dur.base, ease: ease.calm }
                  }
                />
              )}
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
