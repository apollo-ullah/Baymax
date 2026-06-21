import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { brand } from "@/lib/brand";

/**
 * SECTION 01 — THE PROBLEM
 * Names the pain of crisis supply logistics with calm authority. One restrained
 * accent (the live indicator dot + stat figure) carries through the mode-shift.
 */

interface ProblemCard {
  title: string;
  body: string;
  stat: string;
  caption: string;
}

const PROBLEMS: ProblemCard[] = [
  {
    title: "Shortages surface too late",
    body: "By the time a shelf reads empty, the patient is already waiting. Counts are checked by hand on a schedule, so a shortfall is discovered hours after it became unavoidable.",
    stat: "~6 hrs",
    caption: "typical lag between true stock-out and the manual count that catches it",
  },
  {
    title: "Transfers are phone-tag",
    body: "Finding which nearby facility has surplus means a chain of calls, holds, and callbacks. Every handoff loses detail, and the clock keeps running while it happens.",
    stat: "5+ calls",
    caption: "to source one urgent transfer — slow, lossy, and easy to drop",
  },
  {
    title: "Reconciliation lags for weeks",
    body: "Once stock finally moves, settling who owes whom is its own backlog. Paper trails and invoices drift, so the books trail reality long after the supply does.",
    stat: "3–4 wks",
    caption: "between a completed transfer and the payment that finally clears it",
  },
];

export function ProblemSection() {
  return (
    <Section id="problem">
      <header className="max-w-2xl">
        <Reveal>
          <Eyebrow index="01">The problem</Eyebrow>
        </Reveal>

        <Reveal delay={0.06}>
          <h2 className="t-display mode-shift mt-6 text-ink">
            When a surge hits,
            <br className="hidden sm:block" />{" "}
            <span className="text-accent">supply turns into guesswork.</span>
          </h2>
        </Reveal>

        <Reveal delay={0.12}>
          <p className="t-lead mt-6 text-ink-2">
            {brand.name} exists because the hardest part of a shortage is not the
            shelf — it is the hours of phone calls, stale counts, and paperwork
            between a hospital running short and the supply actually arriving.
          </p>
        </Reveal>
      </header>

      <ul className="mt-14 grid grid-cols-1 gap-5 sm:mt-16 md:grid-cols-3">
        {PROBLEMS.map((problem, i) => (
          <Reveal
            key={problem.title}
            as="li"
            delay={0.16 + i * 0.08}
            className="list-none"
          >
            <figure className="mode-shift flex h-full flex-col rounded-xl border border-hairline bg-canvas-raised p-7 shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)]">
              <div
                className="mode-shift flex items-center gap-2 text-accent"
                aria-hidden="true"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                <span className="t-mono text-ink-3">{`0${i + 1}`}</span>
              </div>

              <h3 className="t-title mode-shift mt-5 text-ink">
                {problem.title}
              </h3>

              <p className="t-body mt-3 text-ink-2">{problem.body}</p>

              <figcaption className="mt-auto border-t border-hairline pt-5">
                <span className="t-display mode-shift block text-accent-deep">
                  {problem.stat}
                </span>
                <span className="t-mono mt-2 block text-ink-3">
                  {problem.caption}
                </span>
              </figcaption>
            </figure>
          </Reveal>
        ))}
      </ul>
    </Section>
  );
}
