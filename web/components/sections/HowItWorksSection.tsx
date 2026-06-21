import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { brand } from "@/lib/brand";

interface Step {
  index: string;
  title: string;
  body: string;
}

const steps: Step[] = [
  {
    index: "01",
    title: "Detect the shortfall",
    body: `${brand.name} watches inventory across every facility in the network and projects burn rate forward, so a shortage surfaces as a signal hours or days before the shelf would actually run empty.`,
  },
  {
    index: "02",
    title: "Research the need",
    body: "An agent reasons about the request through ASI:One — what item, how many units, how urgent — and turns a plain-language alert into a precise, structured supply requirement.",
  },
  {
    index: "03",
    title: "Read inventory",
    body: "It reads live stock across facilities, down to camera vision counting physical units on the shelf, so every offer is grounded in what is actually on hand right now.",
  },
  {
    index: "04",
    title: "Reallocate",
    body: "Surplus facilities respond agent-to-agent. Baymax ranks the offers by distance and fit, then composes a single transfer — split across two sources when no one facility can cover the whole need.",
  },
  {
    index: "05",
    title: "Settle",
    body: "Once a human approves in chat, the deal settles as a real on-chain transaction on the Fetch testnet, or places an external supplier order when no internal surplus exists. Fail-closed at every step.",
  },
];

export function HowItWorksSection() {
  return (
    <Section id="how">
      <div className="max-w-2xl">
        <Reveal>
          <Eyebrow index="02">How it works</Eyebrow>
        </Reveal>

        <Reveal delay={0.06}>
          <h2 className="t-display mt-5 text-ink">
            From shortfall to settled,{" "}
            <span className="mode-shift text-accent">in one loop.</span>
          </h2>
        </Reveal>

        <Reveal delay={0.12}>
          <p className="t-lead mt-6 text-ink-2">
            One continuous protocol runs the supply line end to end — detect,
            understand, look, reallocate, settle. Each step hands clean,
            verified state to the next, and a person signs off before anything
            moves.
          </p>
        </Reveal>
      </div>

      <ol className="relative mt-14 sm:mt-20">
        {/* The supply line: a thin rail threaded through the indices. */}
        <div
          aria-hidden="true"
          className="mode-shift pointer-events-none absolute bottom-0 left-5 top-0 w-px bg-hairline-strong sm:left-7"
        >
          {/* the live, accent-lit head of the line */}
          <div className="mode-shift absolute inset-x-0 top-0 h-2/3 bg-gradient-to-b from-accent via-accent/35 to-transparent" />
        </div>

        {steps.map((step, i) => {
          const last = i === steps.length - 1;
          return (
            <Reveal as="li" key={step.index} delay={i * 0.08} y={20}>
              <div className={last ? "flex gap-6 sm:gap-9" : "flex gap-6 pb-12 sm:gap-9 sm:pb-16"}>
                {/* Numbered marker — clinical protocol node on the rail. */}
                <div className="relative z-10 shrink-0">
                  <span
                    className="mode-shift t-mono flex h-10 w-10 items-center justify-center rounded-full border border-hairline-strong bg-canvas-raised text-accent-deep shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)] sm:h-[3.5rem] sm:w-[3.5rem] sm:text-[0.95rem]"
                    aria-hidden="true"
                  >
                    {step.index}
                  </span>
                  {/* soft accent halo, anchoring the node to the live line */}
                  <span
                    aria-hidden="true"
                    className="mode-shift absolute inset-0 -z-10 rounded-full shadow-[0_0_0_6px_rgba(var(--glow),0.06)]"
                  />
                </div>

                <div className="min-w-0 pt-1 sm:pt-2.5">
                  <h3 className="t-title text-ink">
                    <span className="t-mono mr-2 text-ink-3 sm:hidden">
                      {step.index}
                    </span>
                    {step.title}
                  </h3>
                  <p className="t-body mt-3 max-w-xl text-ink-2">{step.body}</p>
                </div>
              </div>
            </Reveal>
          );
        })}
      </ol>
    </Section>
  );
}
