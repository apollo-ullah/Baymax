"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

/**
 * CameraStations
 *
 * Single-laptop, two-facility demo. ONE physical webcam (one getUserMedia stream)
 * is mirrored into two side-by-side stations — Hospital A and Hospital B. You
 * "Scan & freeze" one shelf as Hospital A, physically rearrange the inventory,
 * then "Scan & freeze" the other as Hospital B. Each freeze posts the current
 * frame to /api/scan (→ Flask → Claude Vision) attributed to that hospital_id,
 * which writes hospital:{id}:inventory/surplus + vision:latest into the shared
 * Redis the negotiation reads. A "Rescan" returns a station to the live view.
 *
 * Why one shared stream (not two getUserMedia calls): macOS/Chrome will not open
 * the same camera device twice concurrently — the second grab fails. We open once
 * and assign the same MediaStream to both <video> elements.
 */

interface ScanResult {
  ok: boolean;
  count?: number | null;
  status?: string | null;
  surplus?: number | null;
  item?: string;
  note?: string;
  error?: string;
}

const STATIONS: { hospitalId: string; label: string }[] = [
  { hospitalId: "hospital_a", label: "Hospital A" },
  { hospitalId: "hospital_b", label: "Hospital B" },
];

export function CameraStations({
  item = "Saline",
  className,
}: {
  item?: string;
  className?: string;
}) {
  const streamRef = useRef<MediaStream | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);

  // Open the webcam once; share the stream with both stations.
  useEffect(() => {
    let cancelled = false;
    (async () => {
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
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  return (
    <div className={cn("grid grid-cols-1 gap-4 sm:grid-cols-2", className)}>
      {STATIONS.map((s) => (
        <Station
          key={s.hospitalId}
          hospitalId={s.hospitalId}
          label={s.label}
          item={item}
          stream={streaming ? streamRef.current : null}
          camError={camError}
        />
      ))}
    </div>
  );
}

function Station({
  hospitalId,
  label,
  item,
  stream,
  camError,
}: {
  hospitalId: string;
  label: string;
  item: string;
  stream: MediaStream | null;
  camError: string | null;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [scanning, setScanning] = useState(false);
  const [frozenUrl, setFrozenUrl] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);

  // Attach the shared stream to this station's <video> whenever it changes or we
  // return to the live view (frozenUrl cleared).
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !stream || frozenUrl) return;
    v.srcObject = stream;
    v.play().catch(() => {});
  }, [stream, frozenUrl]);

  const scan = useCallback(async () => {
    const video = videoRef.current;
    if (!video || !stream) return;
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
      setFrozenUrl(dataUrl); // freeze the panel to this still immediately
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hospital_id: hospitalId, item, image_b64: dataUrl }),
      });
      const ct = res.headers.get("content-type") ?? "";
      if (!ct.includes("application/json")) {
        throw new Error(`scan returned ${res.status} (expected JSON)`);
      }
      setResult((await res.json()) as ScanResult);
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "scan failed" });
    } finally {
      setScanning(false);
    }
  }, [stream, hospitalId, item]);

  const rescan = useCallback(() => {
    setFrozenUrl(null);
    setResult(null);
  }, []);

  const live = !frozenUrl;

  return (
    <div className="mode-shift relative overflow-hidden rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <span className="t-eyebrow text-ink-3">{label}</span>
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              frozenUrl ? "bg-sky-400" : stream ? "bg-green-400" : "bg-ink-3",
            )}
            aria-hidden="true"
          />
          <span className="t-mono text-[11px] text-ink-3">
            {frozenUrl ? "Frozen" : stream ? "Live" : "Off"}
          </span>
        </div>
      </div>

      {/* Live video OR frozen still */}
      <div className="relative aspect-video w-full bg-paper-sunken">
        {frozenUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={frozenUrl} alt={`${label} frozen frame`} className="absolute inset-0 h-full w-full object-cover" />
        ) : (
          <video
            ref={videoRef}
            muted
            playsInline
            className={cn(
              "absolute inset-0 h-full w-full object-cover transition-opacity duration-300",
              stream ? "opacity-100" : "opacity-0",
            )}
          />
        )}
        {live && !stream && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center text-ink-3">
            <p className="t-mono text-xs leading-snug">
              {camError ? "Camera unavailable" : "Starting camera…"}
              <br />
              <span className="opacity-60">{camError ?? "allow access when prompted"}</span>
            </p>
          </div>
        )}
        {/* Count badge on the frozen still */}
        {frozenUrl && result?.ok && (
          <div className="absolute bottom-2 left-2 rounded-md bg-black/65 px-2 py-1 t-mono text-xs text-white backdrop-blur-sm">
            {result.count ?? "?"} {result.item ?? item}
            {result.surplus != null ? ` · surplus ${result.surplus}` : ""}
          </div>
        )}
      </div>

      {/* Control + result */}
      <div className="flex items-center justify-between gap-2 border-t border-hairline px-4 py-3">
        {live ? (
          <button
            type="button"
            onClick={scan}
            disabled={!stream || scanning}
            className="mode-shift rounded-pill bg-accent px-3.5 py-1.5 t-mono text-xs text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {scanning ? "Scanning…" : `Scan & freeze ${label.replace("Hospital ", "")}`}
          </button>
        ) : (
          <button
            type="button"
            onClick={rescan}
            disabled={scanning}
            className="mode-shift rounded-pill border border-hairline px-3.5 py-1.5 t-mono text-xs text-ink-2 transition-opacity hover:opacity-80 disabled:opacity-40"
          >
            Rescan
          </button>
        )}
        <div className="t-mono text-right text-[11px] text-ink-3">
          {scanning
            ? "Claude Vision…"
            : result
              ? result.ok
                ? `${result.status ?? "ok"}`
                : "scan failed"
              : "ready"}
        </div>
      </div>

      {result && !result.ok && result.error && (
        <div className="border-t border-hairline px-4 py-2 t-mono text-[11px] leading-snug text-red-500">
          {result.error.slice(0, 160)}
        </div>
      )}
      {result?.ok && result.note && (
        <div className="border-t border-hairline px-4 py-2 t-mono text-[11px] leading-snug text-ink-3">
          {result.note}
        </div>
      )}
    </div>
  );
}
