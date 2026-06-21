/**
 * POST /api/crisis
 *
 * 1. POSTs the prompt to the Flask backend at BAYMAX_API_URL/api/crisis.
 * 2. Opens GET BAYMAX_API_URL/api/narration (SSE; replays full buffer).
 * 3. Translates each SSE frame to a PipelineEvent and streams them as NDJSON.
 * 4. Deduplicates replayed frames via a seen-set keyed on "req_id|state|detail".
 * 5. When final:true arrives, fetches /api/state, derives a CrisisResult, and
 *    writes a trailing NDJSON line tagged {type:"result"}.
 * 6. Closes the stream.
 *
 * The caller (makeLiveApi().runCrisis) reads the NDJSON stream, fires onEvent
 * for every PipelineEvent line, and resolves with the CrisisResult line.
 */

import { NextRequest, NextResponse } from "next/server";
import type { PipelineEvent, StageId, StageStatus, CrisisResult } from "@/lib/api";
import { setLastReqId } from "@/lib/crisis-state";

const BAYMAX_API_URL =
  process.env.BAYMAX_API_URL ?? "http://localhost:5001";

// ── narration-state → stageId / status mapping (plan table, verbatim) ────────

interface StageMapping {
  stageId: StageId;
  status: StageStatus;
  setAwaitingApproval?: true;
}

function mapState(state: string): StageMapping {
  switch (state) {
    case "researching":
      return { stageId: "research", status: "active" };
    case "researched":
      return { stageId: "research", status: "done" };
    case "shortfall_detected":
      return { stageId: "detect", status: "active" };
    case "requesting":
    case "collecting_offers":
      return { stageId: "inventory", status: "active" };
    case "evaluating":
    case "proposing":
      return { stageId: "negotiate", status: "active" };
    case "awaiting_approval":
      return { stageId: "settle", status: "active", setAwaitingApproval: true };
    case "settling":
      return { stageId: "settle", status: "active" };
    case "confirmed":
    case "ordered":
      return { stageId: "settle", status: "done" };
    case "failed":
      return { stageId: "settle", status: "failed" };
    default:
      // Unknown states → detect/active as a safe fallback
      return { stageId: "detect", status: "active" };
  }
}

/** Produce a human-readable title from the narration state. */
function titleFor(state: string, detail?: string): string {
  const truncated =
    detail && detail.length > 60 ? detail.slice(0, 57) + "…" : detail;
  switch (state) {
    case "researching":   return "Researching the need";
    case "researched":    return "Research complete";
    case "shortfall_detected": return "Shortfall detected";
    case "requesting":    return "Requesting surplus offers";
    case "collecting_offers": return "Collecting offers";
    case "evaluating":    return "Evaluating offers";
    case "proposing":     return "Composing transfer plan";
    case "awaiting_approval": return "Awaiting your approval";
    case "settling":      return "Settling on-chain";
    case "confirmed":     return "Transfer confirmed";
    case "ordered":       return "Supplier order placed";
    case "failed":        return "Pipeline failed";
    default:              return truncated ?? state;
  }
}

// ── SSE frame shape from Flask ────────────────────────────────────────────────

interface NarrationFrame {
  req_id: string;
  state: string;
  detail: string;
  final: boolean;
}

// ── Flask /api/state shape (partial — we only need what we use) ───────────────

interface FlaskState {
  item?: string;
  need?: number;
  requester?: string;
  transfers?: Array<{ from: string; to: string; item: string; qty: number }>;
  settlement?: {
    kind?: "transfer" | "order";
    reference?: string;
    amount_fet?: number;
    network?: string;
  };
  reasoning?: string;
  last_frame?: NarrationFrame;
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function POST(req: NextRequest): Promise<NextResponse> {
  let prompt = "";
  try {
    const body = await req.json();
    prompt = String(body.prompt ?? "");
  } catch {
    return NextResponse.json({ error: "Bad request body" }, { status: 400 });
  }

  if (!prompt.trim()) {
    return NextResponse.json({ error: "prompt is required" }, { status: 400 });
  }

  const encoder = new TextEncoder();
  const start = Date.now();

  // Track awaitingApproval across frames so the CrisisResult reflects it.
  let awaitingApproval = false;
  // Track last req_id so the state fetch can be correlated (best effort).
  let lastReqId = "";

  const stream = new ReadableStream({
    async start(controller) {
      const enqueue = (obj: unknown) => {
        controller.enqueue(encoder.encode(JSON.stringify(obj) + "\n"));
      };

      // ── Step 1: POST to Flask /api/crisis ──────────────────────────────────
      try {
        const crisisRes = await fetch(`${BAYMAX_API_URL}/api/crisis`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ crisis_text: prompt }),
        });
        if (!crisisRes.ok) {
          throw new Error(`Flask /api/crisis returned ${crisisRes.status}`);
        }
      } catch (err) {
        // Signal to the client that Flask is down; they will fall back to mock.
        enqueue({ type: "error", message: String(err) });
        controller.close();
        return;
      }

      // ── Step 2: Open SSE stream from Flask /api/narration ─────────────────
      let narrationRes: Response;
      try {
        narrationRes = await fetch(`${BAYMAX_API_URL}/api/narration`, {
          headers: { Accept: "text/event-stream" },
        });
        if (!narrationRes.ok || !narrationRes.body) {
          throw new Error(`Flask /api/narration returned ${narrationRes.status}`);
        }
      } catch (err) {
        enqueue({ type: "error", message: String(err) });
        controller.close();
        return;
      }

      // ── Step 3: Parse SSE frames, deduplicate, translate, stream ──────────
      const seen = new Set<string>();
      const reader = narrationRes.body.getReader();
      const textDecoder = new TextDecoder();
      let buf = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buf += textDecoder.decode(value, { stream: true });

          // SSE frames are separated by double-newline.
          // Each "data: <json>" line is one frame.
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? "";

          for (const part of parts) {
            // Extract data payload from "data: {...}" lines.
            const dataLine = part
              .split("\n")
              .find((l) => l.startsWith("data:"));
            if (!dataLine) continue;

            let frame: NarrationFrame;
            try {
              frame = JSON.parse(dataLine.slice("data:".length).trim());
            } catch {
              continue;
            }

            // Deduplicate replayed SSE frames.
            const key = `${frame.req_id}|${frame.state}|${frame.detail}`;
            if (seen.has(key)) continue;
            seen.add(key);

            if (frame.req_id) {
              lastReqId = frame.req_id;
              setLastReqId(frame.req_id);
            }

            const mapping = mapState(frame.state);
            if (mapping.setAwaitingApproval) {
              awaitingApproval = true;
            }

            const event: PipelineEvent = {
              stageId: mapping.stageId,
              status: mapping.status,
              title: titleFor(frame.state, frame.detail),
              detail: frame.detail || undefined,
              at: Date.now() - start,
            };

            enqueue({ type: "event", event });

            // Close when the backend signals final.
            if (frame.final) {
              break;
            }
          }

          // Also check if the remainder contains a final frame
          // (covers the case where the loop above broke on final).
          if (buf.includes('"final":true') || buf.includes('"final": true')) {
            // Try to parse the buffered partial content.
            const dataLine = buf
              .split("\n")
              .find((l) => l.startsWith("data:"));
            if (dataLine) {
              try {
                const frame: NarrationFrame = JSON.parse(
                  dataLine.slice("data:".length).trim()
                );
                const key = `${frame.req_id}|${frame.state}|${frame.detail}`;
                if (!seen.has(key)) {
                  seen.add(key);
                  if (frame.req_id) {
                    lastReqId = frame.req_id;
                    setLastReqId(frame.req_id);
                  }
                  const mapping = mapState(frame.state);
                  if (mapping.setAwaitingApproval) awaitingApproval = true;
                  enqueue({
                    type: "event",
                    event: {
                      stageId: mapping.stageId,
                      status: mapping.status,
                      title: titleFor(frame.state, frame.detail),
                      detail: frame.detail || undefined,
                      at: Date.now() - start,
                    } satisfies PipelineEvent,
                  });
                }
                if (frame.final) break;
              } catch {
                // ignore partial
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }

      // ── Step 4: Fetch /api/state and emit CrisisResult ────────────────────
      let flaskState: FlaskState = {};
      try {
        const stateRes = await fetch(`${BAYMAX_API_URL}/api/state`);
        if (stateRes.ok) {
          flaskState = await stateRes.json();
        }
      } catch {
        // Best-effort; proceed with defaults.
      }

      const legs: CrisisResult["legs"] = (flaskState.transfers ?? []).map(
        (t) => ({ from: t.from, to: t.to, item: t.item, qty: t.qty })
      );

      const settlement = flaskState.settlement ?? {};
      const lastFrameState = flaskState.last_frame?.state ?? "";
      const isOrder = lastFrameState === "ordered" || settlement.kind === "order";

      const result: CrisisResult = {
        prompt,
        item: flaskState.item ?? "IV fluids",
        needQty: flaskState.need ?? 200,
        target: flaskState.requester ?? "Hospital A",
        summary:
          legs.length > 1
            ? `Split transfer of ${flaskState.need ?? "??"} units of ${flaskState.item ?? "item"} sourced from ${legs.length} facilities.`
            : legs.length === 1
            ? `Transfer of ${legs[0].qty} units of ${legs[0].item} from ${legs[0].from} to ${legs[0].to}.`
            : "Pipeline completed.",
        legs,
        settlement: {
          kind: isOrder ? "order" : "transfer",
          reference: settlement.reference ?? "pending",
          amountFet: settlement.amount_fet ?? 0,
          network: settlement.network ?? "fetchai-testnet (dorado-1)",
        },
        awaitingApproval,
      };

      enqueue({ type: "result", result });
      controller.close();
    },
  });

  return new NextResponse(stream, {
    headers: {
      "Content-Type": "application/x-ndjson",
      "Cache-Control": "no-cache, no-store",
      "X-Accel-Buffering": "no",
    },
  });
}
