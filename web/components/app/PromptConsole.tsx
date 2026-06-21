"use client";

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ease, dur } from "@/lib/motion";
import { cn } from "@/lib/cn";

const EXAMPLES = [
  "Wildfires near Bayview — short on IV fluids",
  "Surge at Northgate, down to 40 units of saline",
  "Highland needs 300 N95 respirators by Friday",
];

export function PromptConsole({
  onRun,
  running,
}: {
  onRun: (prompt: string) => void;
  running: boolean;
}) {
  const [value, setValue] = useState("");
  const reduce = useReducedMotion();

  const submit = () => {
    const v = value.trim();
    if (!v || running) return;
    onRun(v);
  };

  return (
    <div className="mode-shift rounded-xl border border-hairline bg-canvas-raised p-4 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)] sm:p-5">
      <label htmlFor="crisis-prompt" className="t-eyebrow text-ink-3">
        State the crisis
      </label>

      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end">
        <textarea
          id="crisis-prompt"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          rows={2}
          disabled={running}
          placeholder="e.g. Wildfires near Bayview — they're short on IV fluids"
          className={cn(
            "min-h-[3.25rem] w-full resize-none rounded-lg border border-hairline-strong bg-canvas px-4 py-3",
            "t-body text-ink placeholder:text-ink-3",
            "transition-colors focus:border-accent focus:outline-none focus-visible:outline-none",
            "disabled:opacity-60",
          )}
        />
        <motion.button
          type="button"
          onClick={submit}
          disabled={running || !value.trim()}
          whileHover={reduce || running ? undefined : { scale: 1.03 }}
          whileTap={reduce || running ? undefined : { scale: 0.97 }}
          transition={{ duration: dur.micro, ease: ease.press }}
          className={cn(
            "mode-shift inline-flex h-[3.25rem] shrink-0 items-center justify-center gap-2 rounded-lg bg-accent px-6",
            "font-medium text-on-accent shadow-[0_10px_28px_-12px_rgba(var(--glow),0.6)]",
            "disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto",
          )}
        >
          {running ? "Working…" : "Run"}
          {!running && (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M3 8h9M8.5 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </motion.button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="t-mono mr-1 text-ink-3">Try</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            disabled={running}
            onClick={() => {
              setValue(ex);
              onRun(ex);
            }}
            className="mode-shift rounded-pill border border-hairline bg-canvas px-3 py-1.5 text-[0.8rem] text-ink-2 transition-colors hover:border-accent hover:text-ink disabled:opacity-50"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
