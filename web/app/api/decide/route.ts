/**
 * GET /api/decide?decision=approve|order|reject
 *
 * Forwards the admin decision to Flask GET /req/<rid>/{approve,order,reject}.
 * Flask's endpoint is GET (not POST) and returns HTML — we just need the 2xx
 * to know it accepted the command.
 *
 * The req_id comes from the module-level lastReqId written by the crisis route
 * during the most-recent runCrisis call, since decide() takes no id parameter.
 */

import { NextRequest, NextResponse } from "next/server";
import { getLastReqId } from "@/lib/crisis-state";

const BAYMAX_API_URL =
  process.env.BAYMAX_API_URL ?? "http://localhost:5001";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const decision = req.nextUrl.searchParams.get("decision") as
    | "approve"
    | "order"
    | "reject"
    | null;

  if (!decision || !["approve", "order", "reject"].includes(decision)) {
    return NextResponse.json(
      { ok: false, message: "decision must be approve, order, or reject" },
      { status: 400 }
    );
  }

  const rid = getLastReqId();
  if (!rid) {
    // No crisis has been run yet in this process — return a graceful fallback.
    return NextResponse.json(
      { ok: false, message: "No active negotiation to decide on." },
      { status: 404 }
    );
  }

  try {
    const flaskRes = await fetch(
      `${BAYMAX_API_URL}/req/${rid}/${decision}`,
      {
        method: "GET",
        headers: { Accept: "text/html,application/json" },
      }
    );

    if (!flaskRes.ok) {
      return NextResponse.json(
        {
          ok: false,
          message: `Backend returned ${flaskRes.status} for decision "${decision}".`,
        },
        { status: 502 }
      );
    }

    const messages: Record<string, string> = {
      approve: "Approved. Transfer settled on the Fetch testnet — reference posted to the log.",
      order: "Supplier order placed and settled. Tracking on the network.",
      reject: "Plan cancelled. Nothing was transferred.",
    };

    return NextResponse.json({ ok: true, message: messages[decision] });
  } catch (err) {
    return NextResponse.json(
      { ok: false, message: `Failed to reach backend: ${String(err)}` },
      { status: 502 }
    );
  }
}
