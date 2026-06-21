import type { Metadata, Viewport } from "next";
import "./globals.css";
import { fontVariables } from "@/lib/fonts";
import { brand } from "@/lib/brand";
import { CrisisModeProvider } from "@/components/CrisisMode";

export const metadata: Metadata = {
  title: {
    default: `${brand.name} — ${brand.tagline}`,
    template: `%s — ${brand.name}`,
  },
  description: brand.description,
  applicationName: brand.name,
  openGraph: {
    title: `${brand.name} — ${brand.tagline}`,
    description: brand.description,
    type: "website",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f7f5f1",
  colorScheme: "light",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-mode="calm" data-scroll-behavior="smooth" suppressHydrationWarning className={`${fontVariables} h-full`}>
      <body className="min-h-full flex flex-col bg-canvas text-ink">
        <CrisisModeProvider initial="calm">{children}</CrisisModeProvider>
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}
