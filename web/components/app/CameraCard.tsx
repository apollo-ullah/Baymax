"use client";

import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";
import { dur, ease } from "@/lib/motion";

/**
 * CameraCard
 *
 * Shows the live MacBook camera still proxied from the Flask backend
 * (GET /api/image or /api/image?hid=<hospital-id>).  Polls every 3 s with
 * a ?t= cache-buster.  Renders a placeholder when no image is available
 * (backend not running, no camera capture yet, or hardware deferred).
 */

interface CameraCardProps {
  /** Flask hospital id, e.g. "hospital_a". Omit for the latest-across-all view. */
  hospitalId?: string;
  className?: string;
}

// Poll every 3 000 ms.
const POLL_INTERVAL = 3_000;

function buildSrc(hospitalId?: string): string {
  const base = "/api/image";
  const t = Date.now();
  return hospitalId ? `${base}?hid=${encodeURIComponent(hospitalId)}&t=${t}` : `${base}?t=${t}`;
}

export function CameraCard({ hospitalId, className }: CameraCardProps) {
  const reduce = useReducedMotion();

  // `src` is the URL we pass to <img>.  We swap it on every poll tick.
  const [src, setSrc] = useState<string | null>(null);
  // Whether the current src returned a real JPEG (false = placeholder).
  const [hasImage, setHasImage] = useState(false);
  // Track live indicator — blinks when a new frame arrives.
  const [live, setLive] = useState(false);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Probe the proxy once to determine if an image exists, then set up polling.
  const probe = () => {
    const url = buildSrc(hospitalId);
    setSrc(url);
    // We rely on the <img> onLoad / onError to update hasImage.
  };

  useEffect(() => {
    probe();
    intervalRef.current = setInterval(probe, POLL_INTERVAL);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hospitalId]);

  const handleLoad = () => {
    setHasImage(true);
    setLive(true);
    // Reset live indicator after a short pulse so it can re-blink next tick.
    setTimeout(() => setLive(false), 800);
  };

  const handleError = () => {
    // 204 No-Content causes an error event on <img> — treat as no image.
    setHasImage(false);
  };

  const label = hospitalId
    ? `Hospital ${hospitalId.replace("hospital_", "").toUpperCase()} camera`
    : "Live camera";

  return (
    <div
      className={cn(
        "mode-shift relative overflow-hidden rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)]",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4">
        <span className="t-eyebrow text-ink-3">MacBook live view</span>
        <div className="flex items-center gap-2">
          {hasImage && (
            <motion.span
              key={String(live)}
              initial={reduce ? false : { opacity: 0.4 }}
              animate={{ opacity: live ? 1 : 0.35 }}
              transition={{ duration: dur.micro, ease: ease.press }}
              className="inline-block h-2 w-2 rounded-full bg-green-400"
              aria-hidden="true"
            />
          )}
          <span className="t-mono text-ink-3">{label}</span>
        </div>
      </div>

      {/* Image / Placeholder */}
      <div className="relative aspect-video w-full bg-paper-sunken">
        {/* Always mount <img> so we get onLoad/onError feedback each poll.
            Keep it invisible when we have no valid frame so the placeholder
            shows instead. */}
        {src && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={label}
            onLoad={handleLoad}
            onError={handleError}
            className={cn(
              "absolute inset-0 h-full w-full object-cover transition-opacity duration-300",
              hasImage ? "opacity-100" : "opacity-0 pointer-events-none",
            )}
          />
        )}

        {/* Placeholder — shown when no image is available */}
        {!hasImage && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-ink-3">
            {/* Simple camera icon (inline SVG — no dependency) */}
            <svg
              width="40"
              height="40"
              viewBox="0 0 40 40"
              fill="none"
              aria-hidden="true"
            >
              <rect
                x="3"
                y="10"
                width="34"
                height="24"
                rx="4"
                stroke="currentColor"
                strokeWidth="1.8"
              />
              <circle
                cx="20"
                cy="22"
                r="7"
                stroke="currentColor"
                strokeWidth="1.8"
              />
              <path
                d="M14 10V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"
                stroke="currentColor"
                strokeWidth="1.8"
              />
              <circle cx="20" cy="22" r="3" fill="currentColor" opacity="0.25" />
            </svg>
            <p className="t-mono text-center text-sm leading-snug">
              No capture available
              <br />
              <span className="opacity-60">camera deferred</span>
            </p>
          </div>
        )}
      </div>

      {/* Footer status bar */}
      <div className="flex items-center justify-between border-t border-hairline px-5 py-3">
        <span className="t-mono text-ink-3 text-xs">
          {hasImage ? "Streaming · 3 s refresh" : "Waiting for frame…"}
        </span>
        <span className="t-mono text-ink-3 text-xs">
          {hospitalId ?? "all hospitals"}
        </span>
      </div>
    </div>
  );
}
