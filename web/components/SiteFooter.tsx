import Link from "next/link";
import { brand } from "@/lib/brand";

export function SiteFooter() {
  return (
    <footer className="mode-shift border-t border-hairline">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-8 px-5 py-12 sm:px-8 md:flex-row md:items-end md:justify-between">
        <div className="max-w-sm">
          <div className="flex items-center gap-2.5 text-ink">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={brand.logoMarkSrc}
              alt=""
              width={26}
              height={26}
              className="h-[26px] w-[26px]"
            />
            <span className="text-[1.05rem] font-semibold tracking-tight font-[family-name:var(--font-display)]">
              {brand.wordmark}
            </span>
          </div>
          <p className="t-body mt-3 text-ink-2">{brand.tagline}</p>
        </div>

        <div className="flex flex-col gap-2 md:items-end">
          <Link
            href="/app"
            className="text-[0.92rem] text-ink-2 transition-colors hover:text-ink"
          >
            Open dashboard
          </Link>
          <p className="t-mono text-ink-3">
            {brand.domain} · built for the Fetch.ai challenge
          </p>
        </div>
      </div>
    </footer>
  );
}
