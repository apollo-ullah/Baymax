/**
 * GET /api/network
 *
 * Derives a MeshNetwork from Flask GET /api/state (inventory / surplus data).
 * Falls back to defaultNetwork on any error so the UI always has something to
 * render.
 *
 * The Baymax backend exposes three hospitals: hospital_a, hospital_b,
 * hospital_c.  We map them to MeshNode positions compatible with the existing
 * MeshNetwork canvas layout.
 */

import { NextResponse } from "next/server";
import { defaultNetwork, type MeshNetwork, type MeshNode, type FacilityStatus } from "@/lib/mesh";

const BAYMAX_API_URL =
  process.env.BAYMAX_API_URL ?? "http://localhost:5001";

// ── Flask /api/state partial shape ────────────────────────────────────────────

interface HospitalEntry {
  qty?: number;
  surplus?: number;
  capacity?: number;
  status?: string;
}

interface FlaskState {
  inventory?: Record<string, HospitalEntry>;
  transfers?: Array<{ from: string; to: string; item: string; qty: number }>;
}

// ── Hospital A/B/C → MeshNode layout ─────────────────────────────────────────

const HOSPITAL_LAYOUT: Record<
  string,
  { id: string; label: string; short: string; x: number; y: number }
> = {
  hospital_a: { id: "hospital_a", label: "Hospital A", short: "HA", x: 0.25, y: 0.35 },
  hospital_b: { id: "hospital_b", label: "Hospital B", short: "HB", x: 0.75, y: 0.30 },
  hospital_c: { id: "hospital_c", label: "Hospital C", short: "HC", x: 0.50, y: 0.70 },
};

function deriveStatus(entry: HospitalEntry): FacilityStatus {
  const surplus = entry.surplus ?? 0;
  const qty = entry.qty ?? 0;
  const capacity = entry.capacity ?? 500;
  const load = qty / capacity;

  if (surplus > 50) return "surplus";
  if (qty === 0 || load < 0.1) return "critical";
  if (load < 0.25) return "shortage";
  return "stable";
}

export async function GET(): Promise<NextResponse> {
  let state: FlaskState = {};

  try {
    const res = await fetch(`${BAYMAX_API_URL}/api/state`);
    if (res.ok) {
      state = await res.json();
    }
  } catch {
    // Fall back to defaultNetwork below.
  }

  const inventory = state.inventory;
  if (!inventory || Object.keys(inventory).length === 0) {
    return NextResponse.json(defaultNetwork);
  }

  // Build nodes from the hospitals present in inventory.
  const nodes: MeshNode[] = Object.entries(inventory)
    .filter(([id]) => id in HOSPITAL_LAYOUT)
    .map(([id, entry]) => {
      const layout = HOSPITAL_LAYOUT[id];
      const qty = entry.qty ?? 0;
      const capacity = entry.capacity ?? 500;
      const load = Math.min(1, Math.max(0, qty / capacity));
      return {
        id: layout.id,
        label: layout.label,
        short: layout.short,
        x: layout.x,
        y: layout.y,
        load,
        status: deriveStatus(entry),
      };
    });

  if (nodes.length === 0) {
    return NextResponse.json(defaultNetwork);
  }

  // Build edges: A↔B, B↔C, A↔C (triangle topology).
  const edges = [
    { id: "e1", from: "hospital_a", to: "hospital_b" },
    { id: "e2", from: "hospital_b", to: "hospital_c" },
    { id: "e3", from: "hospital_a", to: "hospital_c" },
  ].filter(
    (e) =>
      nodes.some((n) => n.id === e.from) && nodes.some((n) => n.id === e.to)
  );

  const network: MeshNetwork = { nodes, edges };
  return NextResponse.json(network);
}
