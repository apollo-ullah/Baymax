"use client";

import { useRef, useState } from "react";
import { getApi, type CrisisResult, type PipelineEvent } from "@/lib/api";
import { useCrisisMode } from "@/components/CrisisMode";
import { NetworkMesh } from "@/components/NetworkMesh";
import { PromptConsole } from "@/components/app/PromptConsole";
import { PipelinePanel } from "@/components/app/PipelinePanel";
import { ApprovalActions } from "@/components/app/ApprovalActions";
import { ForecastPanel } from "@/components/app/ForecastPanel";
import { CameraCard } from "@/components/app/CameraCard";

type Decision = "approve" | "order" | "reject";

export function Dashboard() {
  const { setMode } = useCrisisMode();
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<CrisisResult | null>(null);
  const [decided, setDecided] = useState<Decision | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [resolution, setResolution] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  const run = async (prompt: string) => {
    ctrl.current?.abort();
    const ac = new AbortController();
    ctrl.current = ac;

    setEvents([]);
    setResult(null);
    setDecided(null);
    setResolution(null);
    setRunning(true);
    setMode("crisis"); // the room warms while the network responds

    try {
      const res = await getApi().runCrisis(
        prompt,
        (e) => setEvents((prev) => [...prev, e]),
        { signal: ac.signal },
      );
      setResult(res);
      setRunning(false);
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return;
      setRunning(false);
      setEvents((prev) => [
        ...prev,
        {
          stageId: "settle",
          status: "failed",
          title: "Pipeline error",
          detail: "Something interrupted the negotiation. Try again.",
          at: prev.length ? prev[prev.length - 1].at + 400 : 0,
        },
      ]);
    }
  };

  const decide = async (d: Decision) => {
    if (!result || deciding) return;
    setDeciding(true);
    const r = await getApi().decide(d);
    setResolution(r.message);
    setDecided(d);
    setDeciding(false);
    setEvents((prev) => [
      ...prev,
      {
        stageId: "settle",
        status: d === "reject" ? "failed" : "done",
        title: d === "reject" ? "Plan cancelled" : "Settled",
        detail: r.message,
        at: prev.length ? prev[prev.length - 1].at + 600 : 0,
      },
    ]);
    if (d === "reject") setMode("calm");
  };

  return (
    <div className="space-y-5">
      <PromptConsole onRun={run} running={running} />

      <div className="grid gap-5 lg:grid-cols-12">
        <div className="min-w-0 space-y-5 lg:col-span-7">
          <PipelinePanel events={events} running={running} />
          {result && (
            <ApprovalActions
              result={result}
              decided={decided}
              deciding={deciding}
              resolution={resolution}
              onDecide={decide}
            />
          )}
        </div>

        <div className="min-w-0 space-y-5 lg:col-span-5">
          <div className="mode-shift relative overflow-hidden rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-44px_rgba(20,25,28,0.3)]">
            <div className="absolute inset-x-0 top-0 z-10 flex items-center justify-between px-5 py-4">
              <span className="t-eyebrow text-ink-3">Live network</span>
              <span className="t-mono text-ink-3">8 facilities</span>
            </div>
            <div className="pointer-events-none absolute inset-x-0 top-0 z-[5] h-20 bg-gradient-to-b from-canvas-raised/90 to-transparent" />
            <NetworkMesh
              className="h-[40vh] min-h-[300px] w-full"
              density={running || result ? 1.6 : 1}
            />
          </div>

          <ForecastPanel />

          <CameraCard />
        </div>
      </div>
    </div>
  );
}
