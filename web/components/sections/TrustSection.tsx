import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { brand } from "@/lib/brand";

/**
 * SECTION 05 — TRUST / CREDIBILITY
 *
 * Makes autonomy feel safe and auditable. Four honest pillars + one clearly
 * illustrative pull-quote attributed to a role only (no invented customers).
 * Restrained accent, generous whitespace.
 */

interface Pillar {
  label: string;
  title: string;
  body: string;
}

const pillars: Pillar[] = [
  {
    label: "Approval gate",
    title: "A human approves every move",
    body: `Nothing transfers and nothing settles until a person says yes in chat. ${brand.name} brings you the plan — the source, the split, the cost — and waits for approve, order, or reject.`,
  },
  {
    label: "On-chain",
    title: "Settled on the ledger",
    body: "Each transfer closes as a real transaction on the Fetch testnet, paid in FET. The settlement reference is yours to verify — the record outlives the conversation.",
  },
  {
    label: "Fail-closed",
    title: "Fail-closed by design",
    body: "If a check fails — inventory unreadable, an offer that doesn't cover the need, a payment that won't verify — it stops and tells you. It never guesses, and it never settles on a maybe.",
  },
  {
    label: "Direct",
    title: "Agent to agent",
    body: "Facilities negotiate with each other directly. Surplus sites answer the need, offers get ranked, and the deal closes between them — no broker sitting in the middle taking a cut.",
  },
];

export function TrustSection() {
  return (
    <Section id="trust">
      <div className="grid gap-12 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-16">
        {/* Left rail — heading + lead, sticky on wide screens */}
        <div className="lg:sticky lg:top-28 lg:self-start">
          <Reveal>
            <Eyebrow index="05">Trust</Eyebrow>
          </Reveal>

          <Reveal delay={0.06}>
            <h2 className="t-display mt-6 text-ink">
              Autonomy you can audit.
            </h2>
          </Reveal>

          <Reveal delay={0.12}>
            <p className="t-lead mt-6 max-w-md">
              {brand.name} acts on its own to keep shelves stocked, but it never
              acts in the dark. Every decision is gated, recorded, and reversible
              up to the moment you approve it.
            </p>
          </Reveal>

          <Reveal delay={0.18}>
            <figure className="mode-shift mt-10 max-w-md rounded-xl border-l-2 border-accent bg-paper-sunken px-6 py-6">
              <blockquote className="t-body text-ink-2">
                &ldquo;I wanted the system to do less without telling me, not
                more. The fact that it stops and asks — and that I can pull up
                the transaction afterward — is what let me trust it on the
                floor.&rdquo;
              </blockquote>
              <figcaption className="t-mono mt-4 text-ink-3">
                <span className="mode-shift text-accent">Illustrative</span>
                {" · "}
                Supply manager, regional hospital network
              </figcaption>
            </figure>
          </Reveal>
        </div>

        {/* Right — the four pillars */}
        <ul className="grid gap-5 sm:grid-cols-2">
          {pillars.map((pillar, i) => (
            <Reveal
              key={pillar.title}
              as="li"
              delay={0.08 + i * 0.06}
              className="h-full"
            >
              <article className="mode-shift flex h-full flex-col rounded-xl border border-hairline bg-canvas-raised p-6 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)] sm:p-7">
                <div className="mode-shift flex items-center gap-2.5 text-accent">
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-accent"
                    aria-hidden="true"
                  />
                  <span className="t-mono uppercase tracking-[0.16em]">
                    {pillar.label}
                  </span>
                </div>

                <h3 className="t-title mt-5 text-ink">{pillar.title}</h3>

                <p className="t-body mt-3 text-ink-2">{pillar.body}</p>
              </article>
            </Reveal>
          ))}
        </ul>
      </div>
    </Section>
  );
}
