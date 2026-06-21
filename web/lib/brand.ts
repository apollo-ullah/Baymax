/**
 * BRAND — the single source of truth for product identity.
 *
 * This is the ONLY place the product name, tagline, and logo are defined.
 * Nothing brand-specific should be hardcoded anywhere else in the app.
 *
 * To rebrand: change the values below and drop your asset(s) into
 * `web/public/brand/`. The placeholder mark there is a stand-in — replace
 * `baymax-mark.svg` (and optionally point `logoSrc` at a full lockup).
 *
 * NOTE: "Baymax" is the working/internal project name. For a public launch
 * pick a clearable mark — this config makes that a one-line change.
 */

export interface Brand {
  /** Full product name, used in headings + metadata. */
  name: string;
  /** Text wordmark shown beside the mark in the nav. */
  wordmark: string;
  /** Short, punchy positioning line (nav, meta description seed). */
  tagline: string;
  /** Longer one-sentence description for <meta> + share cards. */
  description: string;
  /** Icon-only mark. Referenced ONLY through this config. */
  logoMarkSrc: string;
  /** Full logo lockup (falls back to the mark + text wordmark). */
  logoSrc: string;
  /** Product domain, shown in the footer. */
  domain: string;
}

export const brand: Brand = {
  name: "Baymax",
  wordmark: "Baymax",
  tagline: "Critical supply, intelligently networked.",
  description:
    "Baymax is the supply-intelligence layer for hospital networks — it sees a shortfall coming, negotiates a transfer between facilities, and settles it before the shelf runs empty.",
  logoMarkSrc: "/brand/baymax-mark.svg",
  logoSrc: "/brand/baymax-mark.svg",
  domain: "baymax.health",
};
