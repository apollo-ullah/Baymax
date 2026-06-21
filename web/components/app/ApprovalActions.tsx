"use client";

import { motion, useReducedMotion } from "motion/react";
import { ease, dur } from "@/lib/motion";
import type { CrisisResult } from "@/lib/api";
import { cn } from "@/lib/cn";

type Decision = "approve" | "order" | "reject";

export function ApprovalActions({
  result,
  decided,
  deciding,
  resolution,
  onDecide,
}: {
  result: CrisisResult;
  decided: Decision | null;
  deciding: boolean;
  resolution: string | null;
  onDecide: (d: Decision) => void;
}) {
  const reduce = useReducedMotion();
  const totalQty = result.legs.reduce((s, l) => s + l.qty, 0);

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: dur.base, ease: ease.outSoft }}
      className="mode-shift rounded-xl border border-accent bg-accent-soft p-5 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(var(--glow),0.45)] sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="t-eyebrow text-accent-deep mode-shift">
          {decided ? "Resolved" : "Awaiting approval"}
        </span>
        <span className="t-mono break-words text-ink-2">
          {result.settlement.amountFet} FET · {result.settlement.network}
        </span>
      </div>

      <h3 className="t-title mt-3 text-ink">{result.summary}</h3>

      <ul className="mt-4 space-y-2">
        {result.legs.map((leg, i) => (
          <li
            key={i}
            className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.95rem] text-ink-2"
          >
            <span className="mode-shift h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <span className="text-ink">
              <span className="font-medium">{leg.qty}</span> units {leg.item}
            </span>
            <span className="t-mono break-words text-ink-3">
              {leg.from} → {leg.to}
            </span>
          </li>
        ))}
      </ul>

      {!decided ? (
        <div className="mt-6 flex flex-wrap gap-3">
          {(
            [
              { d: "approve", label: "Approve & settle", primary: true },
              { d: "order", label: "Order from supplier", primary: false },
              { d: "reject", label: "Reject", primary: false },
            ] as { d: Decision; label: string; primary: boolean }[]
          ).map((b) => (
            <motion.button
              key={b.d}
              type="button"
              disabled={deciding}
              onClick={() => onDecide(b.d)}
              whileHover={reduce || deciding ? undefined : { scale: 1.03 }}
              whileTap={reduce || deciding ? undefined : { scale: 0.97 }}
              transition={{ duration: dur.micro, ease: ease.press }}
              className={cn(
                "mode-shift inline-flex h-11 items-center justify-center rounded-pill px-5 text-[0.92rem] font-medium disabled:opacity-50",
                b.primary
                  ? "bg-accent text-on-accent shadow-[0_10px_26px_-12px_rgba(var(--glow),0.6)]"
                  : b.d === "reject"
                    ? "border border-hairline-strong bg-transparent text-ink-2 hover:text-ink"
                    : "border border-hairline-strong bg-canvas-raised text-ink hover:border-accent",
              )}
            >
              {b.label}
            </motion.button>
          ))}
        </div>
      ) : (
        <motion.div
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          className="mt-5 flex items-start gap-3 rounded-lg border border-hairline bg-canvas-raised p-4"
        >
          <span
            className={cn(
              "mt-1 h-2 w-2 shrink-0 rounded-full",
              decided === "reject" ? "bg-ink-3" : "bg-teal",
            )}
            aria-hidden="true"
          />
          <div>
            <p className="text-[0.95rem] text-ink">{resolution}</p>
            <p className="t-mono mt-1 text-ink-3">
              {totalQty} units · {decided === "reject" ? "no settlement" : `${result.settlement.amountFet} FET on the Fetch testnet`}
            </p>
          </div>
        </motion.div>
      )}
    </motion.div>
  );
}
