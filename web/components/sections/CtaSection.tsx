import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { Button } from "@/components/ui/Button";
import { Magnetic } from "@/components/ui/Magnetic";
import { brand } from "@/lib/brand";

/**
 * SECTION 06 — Closing CTA.
 * A centered, accent-tinted panel that lands the story with a calm, decisive
 * close. Ambient glow tracks the live accent so it shifts with crisis mode.
 */
export function CtaSection() {
  return (
    <Section className="overflow-hidden py-24 sm:py-32">
      <div className="relative">
        {/* Soft ambient accent glow behind the panel — tracks --glow. */}
        <div
          className="ambient-glow pointer-events-none absolute -inset-x-8 -inset-y-16 sm:-inset-x-16 sm:-inset-y-24"
          aria-hidden="true"
        />

        <Reveal y={28}>
          <div
            className="mode-shift relative mx-auto max-w-3xl overflow-hidden rounded-xl border border-hairline bg-accent-soft px-6 py-16 text-center sm:px-12 sm:py-20 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)]"
          >
            <Reveal delay={0.05}>
              <Eyebrow className="justify-center">Get started</Eyebrow>
            </Reveal>

            <Reveal delay={0.12}>
              <h2 className="t-display mt-7 text-ink">
                Keep every shelf stocked, even on the worst day.
              </h2>
            </Reveal>

            <Reveal delay={0.18}>
              <p className="t-lead mx-auto mt-6 max-w-xl text-ink-2">
                {brand.name} watches the whole network, moves supply between
                facilities before a shortage hits, and settles every transfer
                on-chain. You approve the call. It handles the rest.
              </p>
            </Reveal>

            <Reveal delay={0.24}>
              <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
                <Magnetic>
                  <Button href="/app" variant="primary" size="lg">
                    Open the dashboard
                  </Button>
                </Magnetic>
                <Button href="#how" variant="ghost" size="lg">
                  See how it works
                </Button>
              </div>
            </Reveal>

            <Reveal delay={0.3}>
              <p className="t-mono mt-9 text-ink-3">
                Autonomous agents · Fetch testnet · Human-approved
              </p>
            </Reveal>
          </div>
        </Reveal>
      </div>
    </Section>
  );
}
