/**
 * Thin client between the UI and the Baymax backend.
 *
 * Built offline-first: `mock` mode runs the entire product surface with no
 * backend (deterministic, scripted streams), and `live` mode talks to the
 * Redis-backed agent endpoints through Next route handlers. `live` falls back
 * to `mock` on ANY failure — same fail-closed philosophy as the backend seams,
 * so a demo never hard-fails.
 *
 *   NEXT_PUBLIC_BAYMAX_MODE = mock | live   (default: mock)
 *   BAYMAX_API_URL          = backend base URL (server-side, for the proxy)
 */

import { defaultNetwork, type MeshNetwork } from "@/lib/mesh";

export type Mode = "mock" | "live";

export type StageId =
  | "detect"
  | "research"
  | "inventory"
  | "negotiate"
  | "settle";

export type StageStatus = "pending" | "active" | "done" | "failed";

export interface PipelineStage {
  id: StageId;
  label: string;
  /** What this agent does, one line. */
  blurb: string;
}

export const PIPELINE: PipelineStage[] = [
  { id: "detect", label: "Detect", blurb: "Spot the shortfall and scope the need" },
  { id: "research", label: "Research", blurb: "Understand the item, urgency, and context" },
  { id: "inventory", label: "Inventory", blurb: "Read live stock across the network" },
  { id: "negotiate", label: "Negotiate", blurb: "Rank offers and compose a transfer" },
  { id: "settle", label: "Settle", blurb: "Approve, then settle on-chain or order" },
];

export interface PipelineEvent {
  stageId: StageId;
  status: StageStatus;
  /** Short headline for the event. */
  title: string;
  /** Narration line streamed into the log. */
  detail?: string;
  /** Optional structured payload (offers, legs, tx ref). */
  data?: Record<string, unknown>;
  /** ms since pipeline start. */
  at: number;
}

export interface TransferLeg {
  from: string;
  to: string;
  item: string;
  qty: number;
}

export interface CrisisResult {
  prompt: string;
  item: string;
  needQty: number;
  target: string;
  summary: string;
  legs: TransferLeg[];
  settlement: {
    kind: "transfer" | "order";
    reference: string;
    amountFet: number;
    network: string;
  };
  awaitingApproval: boolean;
}

export interface ForecastPoint {
  label: string;
  /** Observed units up to today (null after today). */
  observed: number | null;
  /** Forecast units from today on (null before today). */
  forecast: number | null;
}

export interface Recommendation {
  id: string;
  item: string;
  qty: number;
  by: string;
  rationale: string;
}

export interface Forecast {
  item: string;
  unit: string;
  threshold: number;
  shortfallLabel: string;
  points: ForecastPoint[];
  recommendations: Recommendation[];
}

export interface BaymaxApi {
  mode: Mode;
  getNetwork(): Promise<MeshNetwork>;
  runCrisis(
    prompt: string,
    onEvent: (e: PipelineEvent) => void,
    opts?: { signal?: AbortSignal; speed?: number },
  ): Promise<CrisisResult>;
  getForecast(opts?: { signal?: AbortSignal }): Promise<Forecast>;
  decide(
    decision: "approve" | "order" | "reject",
  ): Promise<{ ok: boolean; message: string }>;
}

// ── helpers ────────────────────────────────────────────────────────────────

const wait = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });

const NAMES: Record<string, string> = {
  mercy: "Mercy General",
  stanne: "St. Anne's",
  bayview: "Bayview",
  highland: "Highland",
  cedar: "Cedar Park",
  northgate: "Northgate",
  lakeside: "Lakeside",
  riverside: "Riverside",
};

/** Light prompt parsing so the mock responds to what the user typed. */
function parsePrompt(prompt: string): { item: string; target: string; needQty: number } {
  const p = prompt.toLowerCase();
  let item = "IV fluids";
  if (p.includes("saline")) item = "saline";
  else if (p.includes("suture")) item = "sutures";
  else if (p.includes("oxygen")) item = "oxygen cylinders";
  else if (p.includes("blood")) item = "O-neg blood units";
  else if (p.includes("ppe") || p.includes("mask")) item = "N95 respirators";

  let target = "Bayview";
  for (const [, name] of Object.entries(NAMES)) {
    if (p.includes(name.toLowerCase())) {
      target = name;
      break;
    }
  }
  const m = p.match(/(\d{2,4})/);
  const needQty = m ? parseInt(m[1], 10) : 200;
  return { item, target, needQty };
}

// ── mock implementation ──────────────────────────────────────────────────

function makeMockApi(): BaymaxApi {
  return {
    mode: "mock",

    async getNetwork() {
      return defaultNetwork;
    },

    async runCrisis(prompt, onEvent, opts) {
      const signal = opts?.signal;
      const speed = opts?.speed ?? 1;
      const start = performance.now();
      const { item, target, needQty } = parsePrompt(prompt);
      const emit = (e: Omit<PipelineEvent, "at">) =>
        onEvent({ ...e, at: Math.round(performance.now() - start) });
      const beat = (ms: number) => wait(ms / speed, signal);

      // detect
      emit({ stageId: "detect", status: "active", title: "Shortfall detected", detail: `Reading the request: "${prompt.trim()}".` });
      await beat(700);
      emit({ stageId: "detect", status: "done", title: `${target} is short on ${item}`, detail: `Projected need: ${needQty} units. Marking this urgent.` });
      await beat(500);

      // research
      emit({ stageId: "research", status: "active", title: "Researching the need", detail: "Confirming the item, unit size, and how fast it's burning down." });
      await beat(900);
      emit({ stageId: "research", status: "done", title: "Need understood", detail: `${item} · ${needQty} units · acceptable substitutes noted.` });
      await beat(450);

      // inventory
      emit({ stageId: "inventory", status: "active", title: "Reading inventory", detail: "Polling every facility — shelf counts and camera-verified stock." });
      await beat(1000);
      emit({
        stageId: "inventory",
        status: "done",
        title: "Surplus found at 2 facilities",
        detail: `Mercy General: 180 spare · Highland: 90 spare · ${target}: critical.`,
        data: { surplus: [{ id: "mercy", spare: 180 }, { id: "highland", spare: 90 }] },
      });
      await beat(450);

      // negotiate
      emit({ stageId: "negotiate", status: "active", title: "Negotiating transfers", detail: "Requesting offers, ranking by distance and spare capacity." });
      await beat(1000);
      const legs: TransferLeg[] = [
        { from: "Mercy General", to: target, item, qty: Math.min(150, needQty) },
        { from: "Highland", to: target, item, qty: Math.max(0, needQty - 150) },
      ].filter((l) => l.qty > 0);
      emit({
        stageId: "negotiate",
        status: "done",
        title: legs.length > 1 ? "Composed a split transfer" : "Composed a transfer",
        detail: legs.map((l) => `${l.qty} from ${l.from}`).join(" · ") + ` → ${target}.`,
        data: { legs },
      });
      await beat(500);

      // settle (awaiting approval)
      emit({
        stageId: "settle",
        status: "active",
        title: "Awaiting your approval",
        detail: "The plan is ready. Approve to settle on-chain, or order from a supplier instead.",
      });

      const amountFet = legs.reduce((s, l) => s + l.qty, 0) * 0.25;
      return {
        prompt,
        item,
        needQty,
        target,
        summary: `${legs.length > 1 ? "Split" : "Single"} transfer of ${needQty} units of ${item} to ${target}.`,
        legs,
        settlement: {
          kind: "transfer",
          reference: "pending-approval",
          amountFet: Math.round(amountFet * 100) / 100,
          network: "fetchai-testnet (dorado-1)",
        },
        awaitingApproval: true,
      };
    },

    async getForecast(opts) {
      await wait(900, opts?.signal);
      const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"];
      const observed = [420, 388, 360, 318, null, null, null, null, null, null];
      const forecast = [null, null, null, 318, 270, 226, 188, 150, 96, 64];
      const points: ForecastPoint[] = days.map((label, i) => ({
        label,
        observed: observed[i],
        forecast: forecast[i],
      }));
      return {
        item: "IV fluids",
        unit: "units",
        threshold: 120,
        shortfallLabel: "projected shortfall — Tue",
        points,
        recommendations: [
          { id: "r1", item: "IV fluids", qty: 200, by: "Thursday", rationale: "Burn-down crosses the safety threshold in 6 days." },
          { id: "r2", item: "N95 respirators", qty: 500, by: "Monday", rationale: "Forecast surge from regional air-quality alerts." },
        ],
      };
    },

    async decide(decision) {
      await wait(600);
      if (decision === "reject") return { ok: true, message: "Plan cancelled. Nothing was transferred." };
      if (decision === "order") return { ok: true, message: "Supplier order placed and settled. Tracking on the network." };
      return { ok: true, message: "Approved. Transfer settled on the Fetch testnet — reference posted to the log." };
    },
  };
}

// ── live implementation (fails closed to mock) ─────────────────────────────

function makeLiveApi(): BaymaxApi {
  const mock = makeMockApi();
  // The live client calls Next route handlers (which proxy the backend).
  // Until those endpoints exist, every method falls back to mock so the UI
  // never breaks. Wire real fetches here when the backend is up.
  return {
    mode: "live",
    getNetwork: () => mock.getNetwork(),
    runCrisis: (p, cb, o) => mock.runCrisis(p, cb, o),
    getForecast: (o) => mock.getForecast(o),
    decide: (d) => mock.decide(d),
  };
}

let _api: BaymaxApi | null = null;

export function getApi(): BaymaxApi {
  if (_api) return _api;
  const mode = (process.env.NEXT_PUBLIC_BAYMAX_MODE as Mode) || "mock";
  _api = mode === "live" ? makeLiveApi() : makeMockApi();
  return _api;
}

export { NAMES as facilityNames };
