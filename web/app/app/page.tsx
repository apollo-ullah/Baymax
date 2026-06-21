import Link from "next/link";
import type { Metadata } from "next";
import { brand } from "@/lib/brand";
import { StatusPill } from "@/components/StatusPill";
import { CrisisToggle } from "@/components/CrisisToggle";
import { Dashboard } from "@/components/app/Dashboard";

export const metadata: Metadata = { title: "Dashboard" };

export default function AppPage() {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="mode-shift sticky top-0 z-50 border-b border-hairline/70 bg-canvas/72 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between px-5 sm:px-8">
          <Link href="/" className="flex items-center gap-2.5 text-ink">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={brand.logoMarkSrc} alt="" width={28} height={28} className="h-7 w-7" />
            <span className="text-[1.1rem] font-semibold tracking-tight font-[family-name:var(--font-display)]">
              {brand.wordmark}
            </span>
          </Link>
          <div className="flex items-center gap-4">
            <span className="hidden sm:inline-flex">
              <StatusPill />
            </span>
            <CrisisToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-7 px-5 py-9 sm:px-8">
        <div>
          <span className="t-eyebrow text-ink-3">Command surface</span>
          <h1 className="t-display mt-3 text-ink">Respond to a shortfall.</h1>
          <p className="t-lead mt-3 max-w-2xl">
            State a crisis in plain language. {brand.name} researches the need,
            reads inventory across the network, negotiates a transfer, and waits
            for your approval to settle.
          </p>
        </div>

        <Dashboard />
      </main>
    </div>
  );
}
