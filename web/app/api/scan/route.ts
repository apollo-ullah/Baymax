/**
 * POST /api/scan
 *
 * Proxies a browser-captured camera frame to the Flask backend's Claude Vision
 * scan (Flask POST /api/scan). The CameraCard grabs one still from the live
 * webcam and posts it here; we forward and relay the parsed inventory count.
 *
 * If the running Flask build predates /api/scan (404 HTML), falls back to
 * POST /api/capture so the MacBook camera path still works without a restart.
 *
 * Body: { image_b64: string (data URL or base64 JPEG), hospital_id?, item? }
 */
import { NextRequest, NextResponse } from "next/server";

const BAYMAX_API_URL = process.env.BAYMAX_API_URL ?? "http://localhost:5001";

interface ScanBody {
  image_b64?: string;
  hospital_id?: string;
  item?: string;
}

interface ScanPayload {
  ok: boolean;
  hospital_id?: string;
  item?: string;
  count?: number | null;
  status?: string | null;
  surplus?: number | null;
  note?: string;
  error?: string;
}

async function readJson<T>(res: Response): Promise<T | null> {
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) {
    return null;
  }
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function parseVisionOutput(
  output: string,
  hospital_id: string,
  item: string,
): ScanPayload {
  const m = output.match(/qty=(\d+)\s+pct=([\d.]+)\s+status=(\w+)\s+surplus=(\d+)/);
  const dm = output.match(/Detected:\s*\d+\s+\S+\s*[—-]\s*(.+)/);
  return {
    ok: true,
    hospital_id,
    item,
    count: m ? parseInt(m[1], 10) : null,
    status: m ? m[3] : null,
    surplus: m ? parseInt(m[4], 10) : null,
    note: dm ? dm[1].trim() : "",
  };
}

async function scanViaCapture(body: ScanBody): Promise<NextResponse> {
  const hospital_id = body.hospital_id ?? "hospital_a";
  const item = body.item ?? "Saline";
  const upstream = await fetch(`${BAYMAX_API_URL}/api/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hospital_id, item }),
    signal: AbortSignal.timeout(65_000),
  });
  const data = await readJson<{ ok?: boolean; output?: string; error?: string }>(
    upstream,
  );
  if (!data) {
    return NextResponse.json(
      {
        ok: false,
        error: upstream.ok
          ? "Flask /api/capture returned non-JSON"
          : `Flask /api/capture returned ${upstream.status} (expected JSON)`,
      },
      { status: 502 },
    );
  }
  if (!upstream.ok || !data.ok) {
    return NextResponse.json(
      { ok: false, error: data.error ?? `capture failed (${upstream.status})` },
      { status: upstream.ok ? 500 : upstream.status },
    );
  }
  return NextResponse.json(
    parseVisionOutput(data.output ?? "", hospital_id, item),
    { status: 200 },
  );
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  let body: ScanBody;
  try {
    body = (await req.json()) as ScanBody;
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${BAYMAX_API_URL}/api/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(65_000),
    });

    const data = await readJson<ScanPayload>(upstream);
    if (data) {
      return NextResponse.json(data, { status: upstream.status });
    }

    // Stale Flask builds expose /api/capture but not /api/scan (404 HTML).
    if (upstream.status === 404) {
      return scanViaCapture(body);
    }

    const snippet = (await upstream.text()).slice(0, 120).replace(/\s+/g, " ");
    return NextResponse.json(
      {
        ok: false,
        error: `Flask scan backend returned ${upstream.status} (expected JSON). ${snippet}`,
      },
      { status: 502 },
    );
  } catch (err) {
    return NextResponse.json(
      {
        ok: false,
        error:
          err instanceof Error
            ? err.message.includes("fetch failed")
              ? `Cannot reach Flask at ${BAYMAX_API_URL} — is ui/app.py running?`
              : err.message
            : "scan proxy failed",
      },
      { status: 502 },
    );
  }
}
