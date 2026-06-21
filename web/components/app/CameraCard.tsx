"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";
import { dur, ease } from "@/lib/motion";

/**
 * CameraCard
 *
 * Live MacBook webcam via getUserMedia (real-time, in-browser) plus an on-demand
 * "Scan inventory" button: it grabs the current frame, posts it to /api/scan
 * (→ Flask → Claude Vision), and shows the parsed count. No continuous capture —
 * the live view is the browser's own camera; counting happens only on click.
 */

interface CameraCardProps {
  /** Flask hospital id the scan is attributed to. Default hospital_a (this Mac). */
  hospitalId?: string;
  /** Item to count. Default Saline. */
  item?: string;
  className?: string;
}

interface ScanResult {
  ok: boolean;
  count?: number | null;
  status?: string | null;
  surplus?: number | null;
  item?: string;
  note?: string;
  error?: string;
}

export function CameraCard({
  hospitalId = "hospital_a",
  item = "Saline",
  className,
}: CameraCardProps) {
  const reduce = useReducedMotion();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const [streaming, setStreaming] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);

  // Start the live webcam on mount; stop all tracks on unmount.
  useEffect(() => {
    let cancelled = false;
    async function start() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
        setStreaming(true);
        setCamError(null);
      } catch (e) {
        setCamError(
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Camera permission denied"
            : e instanceof Error
              ? e.message
              : "Camera unavailable",
        );
        setStreaming(false);
      }
    }
    start();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  const handleScan = useCallback(async () => {
    const video = videoRef.current;
    if (!video || !streaming) return;
    setScanning(true);
    setResult(null);
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas unavailable");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hospital_id: hospitalId, item, image_b64: dataUrl }),
      });
      setResult((await res.json()) as ScanResult);
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "scan failed" });
    } finally {
      setScanning(false);
    }
  }, [streaming, hospitalId, item]);

  const facility = `Hospital ${hospitalId.replace("hospital_", "").toUpperCase()}`;

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
          <motion.span
            key={String(streaming)}
            initial={reduce ? false : { opacity: 0.4 }}
            animate={{ opacity: streaming ? 1 : 0.3 }}
            transition={{ duration: dur.micro, ease: ease.press }}
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              streaming ? "bg-green-400" : "bg-ink-3",
            )}
            aria-hidden="true"
          />
          <span className="t-mono text-ink-3">
            {streaming ? "Live camera" : "Camera off"}
          </span>
        </div>
      </div>

      {/* Live video / fallback */}
      <div className="relative aspect-video w-full bg-paper-sunken">
        <video
          ref={videoRef}
          muted
          playsInline
          className={cn(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-300",
            streaming ? "opacity-100" : "opacity-0 pointer-events-none",
          )}
        />
        {!streaming && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center text-ink-3">
            <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
              <rect x="3" y="10" width="34" height="24" rx="4" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="20" cy="22" r="7" stroke="currentColor" strokeWidth="1.8" />
              <path d="M14 10V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="20" cy="22" r="3" fill="currentColor" opacity="0.25" />
            </svg>
            <p className="t-mono text-sm leading-snug">
              {camError ? "Camera unavailable" : "Starting camera…"}
              <br />
              <span className="opacity-60">{camError ?? "allow access when prompted"}</span>
            </p>
          </div>
        )}
      </div>

      {/* Scan control + result */}
      <div className="flex items-center justify-between gap-3 border-t border-hairline px-5 py-3">
        <button
          type="button"
          onClick={handleScan}
          disabled={!streaming || scanning}
          className="mode-shift rounded-pill bg-accent px-4 py-2 t-mono text-sm text-white transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {scanning ? "Scanning…" : "Scan inventory"}
        </button>
        <div className="t-mono text-right text-xs text-ink-3">
          {result
            ? result.ok
              ? `${result.item ?? item}: ${result.count ?? "?"} · ${result.status ?? ""}${
                  result.surplus != null ? ` · surplus ${result.surplus}` : ""
                }`
              : `scan failed`
            : facility}
        </div>
      </div>

      {result && !result.ok && result.error && (
        <div className="border-t border-hairline px-5 py-2 t-mono text-[11px] leading-snug text-red-500">
          {result.error.slice(0, 160)}
        </div>
      )}
      {result?.ok && result.note && (
        <div className="border-t border-hairline px-5 py-2 t-mono text-[11px] leading-snug text-ink-3">
          {result.note}
        </div>
      )}
    </div>
  );
}
