"use client";

import Link from "next/link";
import { brand } from "@/lib/brand";
import { Button } from "@/components/ui/Button";

const LINKS = [
  { href: "#problem", label: "The problem" },
  { href: "#how", label: "How it works" },
  { href: "#network", label: "Network" },
];

export function SiteNav() {
  return (
    <header className="sticky top-0 z-50">
      <div className="mode-shift border-b border-hairline/70 bg-canvas/72 backdrop-blur-xl">
        <nav className="mx-auto flex h-16 max-w-[1240px] items-center justify-between px-5 sm:px-8">
          <Link
            href="/"
            className="flex items-center gap-2.5 text-ink"
            aria-label={`${brand.name} home`}
          >
            {/* Logo is referenced ONLY through brand.ts (swap the asset there). */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={brand.logoMarkSrc}
              alt=""
              width={30}
              height={30}
              className="h-[30px] w-[30px]"
            />
            <span className="text-[1.15rem] font-semibold tracking-tight font-[family-name:var(--font-display)]">
              {brand.wordmark}
            </span>
          </Link>

          <div className="hidden items-center gap-9 md:flex">
            {LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="text-[0.92rem] text-ink-2 transition-colors hover:text-ink"
              >
                {l.label}
              </a>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <Button href="/app" size="md" variant="primary">
              Open dashboard
            </Button>
          </div>
        </nav>
      </div>
    </header>
  );
}
