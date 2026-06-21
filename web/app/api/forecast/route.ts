/**
 * GET /api/forecast
 *
 * Derives a Forecast from Flask GET /api/state (fields: forecast, reasoning,
 * item, need).  Falls back to the mock seed shape on any failure.
 */

import { NextResponse } from "next/server";
import type { Forecast, ForecastPoint, Recommendation } from "@/lib/api";

const BAYMAX_API_URL =
  process.env.BAYMAX_API_URL ?? "http://localhost:5001";

// ── mock seed (matches makeMockApi().getForecast) ─────────────────────────────

function mockForecast(): Forecast {
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
      {
        id: "r1",
        item: "IV fluids",
        qty: 200,
        by: "Thursday",
        rationale: "Burn-down crosses the safety threshold in 6 days.",
      },
      {
        id: "r2",
        item: "N95 respirators",
        qty: 500,
        by: "Monday",
        rationale: "Forecast surge from regional air-quality alerts.",
      },
    ],
  };
}

// ── Flask /api/state partial shape ────────────────────────────────────────────

interface FlaskState {
  item?: string;
  need?: number;
  forecast?: Array<{ label: string; observed: number | null; forecast: number | null }>;
  reasoning?: string;
  recommendations?: Array<{ id: string; item: string; qty: number; by: string; rationale: string }>;
}

export async function GET(): Promise<NextResponse> {
  let state: FlaskState = {};

  try {
    const res = await fetch(`${BAYMAX_API_URL}/api/state`);
    if (res.ok) {
      state = await res.json();
    }
  } catch {
    // Fall back to mock shape below.
  }

  // If Flask returned useful forecast data, use it; otherwise fall back.
  const hasForecast =
    Array.isArray(state.forecast) && state.forecast.length > 0;

  if (!hasForecast) {
    return NextResponse.json(mockForecast());
  }

  const points: ForecastPoint[] = state.forecast!.map((p) => ({
    label: p.label,
    observed: p.observed,
    forecast: p.forecast,
  }));

  // Derive threshold: 30% of the max observed value (or 120 if absent).
  const maxObserved = Math.max(
    ...points.map((p) => p.observed ?? 0)
  );
  const threshold = maxObserved > 0 ? Math.round(maxObserved * 0.3) : 120;

  // Build recommendations from Flask or seed with one generic recommendation.
  const recommendations: Recommendation[] = Array.isArray(state.recommendations)
    ? state.recommendations
    : [
        {
          id: "r1",
          item: state.item ?? "IV fluids",
          qty: state.need ?? 200,
          by: "Thursday",
          rationale: state.reasoning ?? "Burn-down analysis indicates a shortfall within 6 days.",
        },
      ];

  const forecast: Forecast = {
    item: state.item ?? "IV fluids",
    unit: "units",
    threshold,
    shortfallLabel: "projected shortfall — Tue",
    points,
    recommendations,
  };

  return NextResponse.json(forecast);
}
