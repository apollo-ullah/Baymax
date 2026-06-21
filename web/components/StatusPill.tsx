"use client";

import { useCrisisMode } from "@/components/CrisisMode";
import { cn } from "@/lib/cn";

/**
 * Live system status. Reads the environmental state and speaks plainly:
 * calm = all-clear; crisis = action in progress.
 */
export function StatusPill({ className }: { className?: string }) {
  const { isCrisis } = useCrisisMode();
  return (
    <span
      className={cn(
        "mode-shift inline-flex items-center gap-2.5 rounded-pill border border-hairline-strong bg-canvas-raised py-1.5 pl-2.5 pr-4",
        className,
      )}
    >
      <span className="relative flex h-2.5 w-2.5">
        <span
          className={cn(
            "absolute inline-flex h-full w-full rounded-full bg-accent opacity-60",
            !isCrisis ? "motion-safe:animate-ping" : "motion-safe:animate-ping",
          )}
        />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
      </span>
      <span className="t-mono text-ink-2">
        {isCrisis ? "Active crisis — rerouting supply" : "All facilities nominal"}
      </span>
    </span>
  );
}
