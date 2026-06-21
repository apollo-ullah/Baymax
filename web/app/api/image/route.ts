/**
 * GET /api/image
 *
 * Proxies Flask camera endpoints and returns raw JPEG bytes.
 *
 * Routes:
 *   /api/image              → Flask GET /image/latest
 *   /api/image?hid=<hid>   → Flask GET /image/hospital/<hid>
 *
 * Returns 204 (no content) when Flask has no image yet so the caller can
 * treat it as "camera not ready" without an error.  Any network failure also
 * returns 204 so the UI falls back to its placeholder silently.
 */

import { NextRequest, NextResponse } from "next/server";

const BAYMAX_API_URL =
  process.env.BAYMAX_API_URL ?? "http://localhost:5001";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const hid = req.nextUrl.searchParams.get("hid");
  const endpoint = hid
    ? `${BAYMAX_API_URL}/image/hospital/${encodeURIComponent(hid)}`
    : `${BAYMAX_API_URL}/image/latest`;

  try {
    const upstream = await fetch(endpoint, {
      // Short timeout — camera frame should be fast; don't hang the UI.
      signal: AbortSignal.timeout(4000),
    });

    if (!upstream.ok) {
      // 404 = no image yet; any other error = treat as not-ready.
      return new NextResponse(null, { status: 204 });
    }

    const contentType =
      upstream.headers.get("content-type") ?? "image/jpeg";
    const bytes = await upstream.arrayBuffer();

    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        // Let the browser cache for a maximum of 1s; the component adds its
        // own ?t= cache-buster anyway so this just avoids duplicate fetches
        // within the same polling tick.
        "Cache-Control": "public, max-age=1",
      },
    });
  } catch {
    // Network error, timeout, etc. — return no-content so the card shows
    // the placeholder without surfacing an error to the user.
    return new NextResponse(null, { status: 204 });
  }
}
