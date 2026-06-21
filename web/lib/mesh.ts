/**
 * The data model behind the signature visual — the living hospital network.
 *
 * One shape serves two surfaces: the landing hero runs a self-driven ambient
 * demo over `defaultNetwork`; the live dashboard will feed the same component
 * real facilities + transfers from the backend. Keep this model thin and
 * presentation-agnostic.
 */

export type FacilityStatus = "stable" | "surplus" | "shortage" | "critical";

export interface MeshNode {
  id: string;
  label: string;
  /** 2–3 char glyph drawn at the node in dense views. */
  short: string;
  /** Normalized position in the canvas box, 0..1. */
  x: number;
  y: number;
  /** Inventory pressure, 0 (empty) .. 1 (full). Drives node size + ring. */
  load: number;
  status: FacilityStatus;
}

export interface MeshEdge {
  id: string;
  from: string;
  to: string;
}

export interface MeshNetwork {
  nodes: MeshNode[];
  edges: MeshEdge[];
}

/** A live reallocation traveling an edge (from → to). */
export interface Transfer {
  id: string;
  edgeId: string;
  /** 0..1 progress along the edge. */
  progress: number;
}

/**
 * Eight facilities in a loose, organic constellation (not a grid — grids read
 * as charts, not networks). Positions are hand-placed for visual balance.
 */
export const defaultNetwork: MeshNetwork = {
  nodes: [
    { id: "mercy", label: "Mercy General", short: "MG", x: 0.18, y: 0.30, load: 0.82, status: "surplus" },
    { id: "stanne", label: "St. Anne's", short: "SA", x: 0.40, y: 0.16, load: 0.61, status: "stable" },
    { id: "bayview", label: "Bayview", short: "BV", x: 0.64, y: 0.24, load: 0.28, status: "shortage" },
    { id: "highland", label: "Highland", short: "HL", x: 0.84, y: 0.44, load: 0.74, status: "surplus" },
    { id: "cedar", label: "Cedar Park", short: "CP", x: 0.30, y: 0.58, load: 0.52, status: "stable" },
    { id: "northgate", label: "Northgate", short: "NG", x: 0.55, y: 0.66, load: 0.14, status: "critical" },
    { id: "lakeside", label: "Lakeside", short: "LK", x: 0.76, y: 0.74, load: 0.69, status: "stable" },
    { id: "riverside", label: "Riverside", short: "RV", x: 0.13, y: 0.74, load: 0.58, status: "stable" },
  ],
  edges: [
    { id: "e1", from: "mercy", to: "stanne" },
    { id: "e2", from: "stanne", to: "bayview" },
    { id: "e3", from: "bayview", to: "highland" },
    { id: "e4", from: "mercy", to: "cedar" },
    { id: "e5", from: "cedar", to: "northgate" },
    { id: "e6", from: "northgate", to: "lakeside" },
    { id: "e7", from: "highland", to: "lakeside" },
    { id: "e8", from: "cedar", to: "riverside" },
    { id: "e9", from: "stanne", to: "cedar" },
    { id: "e10", from: "bayview", to: "northgate" },
    { id: "e11", from: "riverside", to: "northgate" },
  ],
};

/** RGB triplets (so we can build rgba glows + lerp between states on canvas). */
export const meshPalette = {
  calm: {
    edge: [28, 140, 125], // teal
    node: [18, 101, 90], // teal-deep
    flow: [40, 168, 150],
  },
  crisis: {
    edge: [226, 92, 61], // coral
    node: [178, 62, 38], // coral-deep
    flow: [242, 162, 78], // amber
  },
  ink: [21, 25, 28],
} as const;

export const statusTint: Record<FacilityStatus, [number, number, number]> = {
  stable: [28, 140, 125],
  surplus: [40, 168, 150],
  shortage: [242, 162, 78],
  critical: [226, 92, 61],
};
