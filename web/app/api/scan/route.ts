/**
 * POST /api/scan
 *
 * Proxies a browser-captured camera frame to the Flask backend's Claude Vision
 * scan (Flask POST /api/scan). The CameraCard grabs one still from the live
 * webcam and posts it here; we forward and relay the parsed inventory count.
 *
 * Body: { image_b64: string (data URL or base64 JPEG), hospital_id?, item? }
 */
import { NextRequest, NextResponse } from "next/server";

const BAYMAX_API_URL = process.env.BAYMAX_API_URL ?? "http://localhost:5001";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const body = await req.json();
    const upstream = await fetch(`${BAYMAX_API_URL}/api/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // Claude Vision can take a few seconds; give it room.
      signal: AbortSignal.timeout(65_000),
    });
    const data = await upstream.json();
    return NextResponse.json(data, { status: upstream.status });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : "scan proxy failed" },
      { status: 502 },
    );
  }
}
