"use client";

import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  PIPELINE,
  type PipelineEvent,
  type StageId,
  type StageStatus,
} from "@/lib/api";
import { cn } from "@/lib/cn";

function deriveStatus(events: PipelineEvent[]): Record<StageId, StageStatus> {
  const map: Record<string, StageStatus> = {};
  for (const s of PIPELINE) map[s.id] = "pending";
  for (const e of events) map[e.stageId] = e.status;
  return map as Record<StageId, StageStatus>;
}

const STAGE_LABEL: Record<StageId, string> = Object.fromEntries(
  PIPELINE.map((s) => [s.id, s.label]),
) as Record<StageId, string>;

function StageDot({ status }: { status: StageStatus }) {
  const reduce = useReducedMotion();
  if (status === "done") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-on-accent mode-shift">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M2.5 6.2 4.8 8.5 9.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-coral text-white">
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M3 3 9 9M9 3 3 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </span>
    );
  }
  if (status === "active") {
    return (
      <span className="relative flex h-6 w-6 items-center justify-center">
        <span className="absolute h-6 w-6 rounded-full border-2 border-accent/30 mode-shift" />
        <motion.span
          className="absolute h-6 w-6 rounded-full border-2 border-transparent border-t-accent mode-shift"
          animate={reduce ? undefined : { rotate: 360 }}
          transition={{ duration: 0.9, ease: "linear", repeat: Infinity }}
        />
        <span className="h-1.5 w-1.5 rounded-full bg-accent mode-shift" />
      </span>
    );
  }
  return <span className="h-6 w-6 rounded-full border border-hairline-strong" />;
}

function StageRail({ statuses }: { statuses: Record<StageId, StageStatus> }) {
  return (
    <ol className="flex items-start gap-1 overflow-x-auto pb-1">
      {PIPELINE.map((s, i) => {
        const st = statuses[s.id];
        return (
          <li key={s.id} className="flex flex-1 items-start gap-1">
            <div className="flex min-w-[64px] flex-col items-center gap-2 text-center">
              <StageDot status={st} />
              <span
                className={cn(
                  "t-mono text-[0.7rem] leading-tight transition-colors",
                  st === "pending" ? "text-ink-3" : "text-ink",
                )}
              >
                {s.label}
              </span>
            </div>
            {i < PIPELINE.length - 1 && (
              <span
                className={cn(
                  "mode-shift mt-3 h-px flex-1 transition-colors",
                  statuses[PIPELINE[i + 1].id] !== "pending" || st === "done"
                    ? "bg-accent/50"
                    : "bg-hairline",
                )}
                aria-hidden="true"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function LogEntry({ event, index }: { event: PipelineEvent; index: number }) {
  const reduce = useReducedMotion();
  const seconds = (event.at / 1000).toFixed(1);
  return (
    <motion.li
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
      className="flex gap-3 py-2.5"
    >
      <span className="t-mono shrink-0 pt-0.5 text-ink-3 tabular-nums">
        {seconds}s
      </span>
      <span
        className={cn(
          "mode-shift mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
          event.status === "failed" ? "bg-coral" : "bg-accent",
        )}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="t-mono text-[0.7rem] uppercase tracking-wider text-ink-3">
            {STAGE_LABEL[event.stageId]}
          </span>
          <span className="text-[0.95rem] font-medium text-ink">{event.title}</span>
        </p>
        {event.detail && (
          <p className="mt-0.5 text-[0.92rem] leading-relaxed text-ink-2">
            {event.detail}
          </p>
        )}
      </div>
    </motion.li>
  );
}

export function PipelinePanel({
  events,
  running,
}: {
  events: PipelineEvent[];
  running: boolean;
}) {
  const statuses = deriveStatus(events);
  const logRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  return (
    <div className="mode-shift flex flex-col rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)]">
      <div className="border-b border-hairline px-5 py-4 sm:px-6">
        <div className="flex items-center justify-between">
          <span className="t-eyebrow text-ink-3">Live pipeline</span>
          <span className="t-mono text-ink-3">
            {running ? "negotiating…" : events.length ? "complete" : "idle"}
          </span>
        </div>
        <div className="mt-5">
          <StageRail statuses={statuses} />
        </div>
      </div>

      <ol
        ref={logRef}
        className="max-h-[42vh] min-h-[180px] divide-y divide-hairline/60 overflow-y-auto px-5 py-2 sm:px-6"
        aria-live="polite"
        aria-label="Pipeline narration"
      >
        {events.length === 0 ? (
          <li className="flex h-[180px] flex-col items-center justify-center gap-2 text-center">
            <p className="t-body text-ink-2">No active negotiation.</p>
            <p className="t-mono text-ink-3">
              Describe a crisis above to watch the network respond.
            </p>
          </li>
        ) : (
          events.map((e, i) => <LogEntry key={i} event={e} index={i} />)
        )}
      </ol>
    </div>
  );
}
