# Baymax — Web

The marketing landing page and product dashboard for Baymax, the supply-intelligence
layer for hospital networks. Built standalone so it can ship on its own and be wired
to the agent backend later.

> **Aesthetic:** calm competence under pressure. Warm off-white canvas + medical
> teal at rest; the whole palette warms to coral/amber when a crisis is active.

## Stack

- **Next.js 16** (App Router) + **React 19** + **TypeScript**
- **Tailwind CSS v4** (CSS-first `@theme` tokens, no JS config)
- **motion** (Framer Motion) for entrance, scroll, and micro-interactions
- **Canvas 2D** for the signature live network mesh

## Run it

```bash
cd web
npm install
npm run dev      # http://localhost:3000
```

```bash
npm run build && npm run start   # production
```

No backend is required — the UI runs fully on mock data (see **Mock vs live**).

Useful URLs while developing:

| URL | What |
| :-- | :-- |
| `/` | Landing page (cinematic hero + live mesh) |
| `/app` | Product dashboard surface |
| `/?mode=crisis` | Deep-link the crisis state (the palette shift) — also on `/app` |

## The design system

Everything derives from a small, deliberate token set:

- **Color** — `app/globals.css`. Calm = `--paper #F7F5F1` + `--teal #1C8C7D`.
  Crisis = warmer paper + `--coral #E25C3D` / `--amber`. The signature lives in
  `[data-mode="crisis"]`, which re-points the semantic tokens (`--accent`,
  `--canvas`, `--glow`, `--status`) so a single attribute warms the whole UI.
- **Type** — `lib/fonts.ts`. Clash Display (display) / General Sans (body),
  both self-hosted variable woff2 in `app/fonts/`; IBM Plex Mono (data/telemetry).
  Scale lives in `globals.css` as `.t-hero`, `.t-display`, `.t-lead`, etc.
- **Motion** — `lib/motion.ts`. Three reused curves (`outSoft`, `calm`, `press`)
  and a tight set of durations. `prefers-reduced-motion` is honored globally.
- **Signature visual** — `components/NetworkMesh.tsx`. A Canvas-2D hospital
  network: nodes = facilities (sized by load, tinted by status), edges = supply
  routes, transfers light up and flow along edges. DPR-aware, pauses when the tab
  is hidden, renders a static composed frame under reduced motion, and degrades
  on coarse-pointer/mobile. Feeds both the hero and the dashboard.

The crisis state is exposed through `components/CrisisMode.tsx`
(`useCrisisMode()` / `<CrisisToggle/>`). On the landing page a demo toggle drives
it; in production it will be driven by the backend crisis signal.

## Branding (logo-agnostic)

All product identity lives in **`lib/brand.ts`** — name, tagline, description,
and `logoSrc` / `logoMarkSrc`. Nothing brand-specific is hardcoded anywhere else.

**To rebrand:** edit `lib/brand.ts` and drop your asset into `public/brand/`
(replace `baymax-mark.svg`). The placeholder mark uses `currentColor`.

## Mock vs live

The UI is built to run offline first, then flip to live — the same fail-closed
philosophy as the backend seams.

- **Now:** the mesh runs on `lib/mesh.ts` (`defaultNetwork`) with a self-driven
  ambient transfer simulation. Fully offline.
- **Next:** a thin client (`lib/api.ts`) behind an interface with a `mock` mode
  and a `live` mode that talks to the Redis-backed dashboard/agent endpoints
  (via Next route handlers as a CORS/env proxy). `live` falls back to `mock` on
  any failure, so the demo never hard-fails.

Configure via `.env.local` (gitignored):

```bash
# NEXT_PUBLIC_BAYMAX_MODE=mock | live   (default: mock)
# BAYMAX_API_URL=http://localhost:8000  (live backend base URL)
```

## Deploy

Vercel-ready. Import the repo, set the project root to `web/`, add any
`.env` values, and deploy. Pages are statically prerendered; the mesh and
interactions hydrate on the client.

## Accessibility & performance

- Semantic HTML, visible keyboard focus, `prefers-reduced-motion` respected
  everywhere (mesh + entrance + interactions).
- Fonts self-hosted (no CDN), subset + `display: swap`.
- Mesh animation pauses on hidden tabs and caps device pixel ratio.
