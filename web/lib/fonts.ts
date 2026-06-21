import localFont from "next/font/local";
import { IBM_Plex_Mono } from "next/font/google";

/**
 * Type system — committed pairing:
 *   Display  → Clash Display (Fontshare)  — oversized editorial headlines
 *   Body     → General Sans (Fontshare)   — humanist, highly legible
 *   Data     → IBM Plex Mono (Google)     — clinical "telemetry" voice for
 *                                            stats, timestamps, agent narration
 *
 * Fontshare faces are self-hosted (variable woff2 in app/fonts) so there is no
 * runtime CDN dependency — keeps Lighthouse + privacy clean and CSP simple.
 */

export const clashDisplay = localFont({
  src: "../app/fonts/ClashDisplay-Variable.woff2",
  variable: "--font-clash",
  display: "swap",
  weight: "200 700",
  fallback: ["ui-sans-serif", "system-ui", "sans-serif"],
});

export const generalSans = localFont({
  src: "../app/fonts/GeneralSans-Variable.woff2",
  variable: "--font-general",
  display: "swap",
  weight: "200 700",
  fallback: ["ui-sans-serif", "system-ui", "sans-serif"],
});

export const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-plex-mono",
  weight: ["400", "500"],
  display: "swap",
});

export const fontVariables = [
  clashDisplay.variable,
  generalSans.variable,
  plexMono.variable,
].join(" ");
