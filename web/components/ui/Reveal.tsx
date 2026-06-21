"use client";

import { motion, useReducedMotion } from "motion/react";
import { ease, dur, inView } from "@/lib/motion";
import { cn } from "@/lib/cn";

/**
 * Scroll-reveal wrapper — the house entrance for content as it enters view.
 * Collapses to a plain div under prefers-reduced-motion (no transform, fully
 * visible). Use `delay` to orchestrate small sequences within a section.
 */
export function Reveal({
  children,
  className,
  delay = 0,
  y = 22,
  as = "div",
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
  y?: number;
  as?: "div" | "li" | "span";
}) {
  const reduce = useReducedMotion();
  const Tag = motion[as];

  if (reduce) {
    const Plain = as;
    return <Plain className={className}>{children}</Plain>;
  }

  return (
    <Tag
      className={cn(className)}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={inView}
      transition={{ duration: dur.base, ease: ease.outSoft, delay }}
    >
      {children}
    </Tag>
  );
}
