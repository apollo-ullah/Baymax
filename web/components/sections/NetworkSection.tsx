import { Section } from "@/components/ui/Section";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Reveal } from "@/components/ui/Reveal";
import { NetworkMesh } from "@/components/NetworkMesh";
import { brand } from "@/lib/brand";

const legend = [
  {
    label: "Surplus",
    dot: "bg-teal",
    meaning: "Stock to spare — a likely source for the next transfer.",
  },
  {
    label: "Stable",
    dot: "bg-ink-2",
    meaning: "Holding above its safety threshold. Nothing to do.",
  },
  {
    label: "Shortage",
    dot: "bg-amber",
    meaning: "Trending toward empty. A transfer is being composed.",
  },
  {
    label: "Critical",
    dot: "bg-coral",
    meaning: "Below the line. Settling now, or escalating to a supplier.",
  },
] as const;

export function NetworkSection() {
  return (
    <Section id="network">
      <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:items-center lg:gap-16">
        {/* Narrative column */}
        <div className="flex flex-col">
          <Reveal>
            <Eyebrow index="03">The network</Eyebrow>
          </Reveal>

          <Reveal delay={0.06}>
            <h2 className="t-display mode-shift mt-6 text-ink">
              A living map of every facility and every transfer.
            </h2>
          </Reveal>

          <Reveal delay={0.12}>
            <p className="t-lead mt-6 text-ink-2">
              Each node is a facility, sized by how full its shelves are. The
              lines between them are supply transfers, lighting up the moment{" "}
              {brand.name} moves stock from a facility with room to spare toward
              one that is running low.
            </p>
          </Reveal>

          <Reveal delay={0.18}>
            <ul className="mode-shift mt-10 grid grid-cols-1 gap-x-8 gap-y-6 border-t border-hairline pt-8 sm:grid-cols-2">
              {legend.map((item) => (
                <li key={item.label} className="flex items-start gap-3">
                  <span
                    className={`${item.dot} mt-[0.45rem] h-2 w-2 shrink-0 rounded-full`}
                    aria-hidden="true"
                  />
                  <span className="flex flex-col gap-1">
                    <span className="t-mono text-ink">{item.label}</span>
                    <span className="t-body text-ink-3">{item.meaning}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>

        {/* Mesh column */}
        <Reveal delay={0.1} className="min-w-0">
          <figure className="mode-shift overflow-hidden rounded-xl border border-hairline bg-canvas-raised shadow-[0_1px_2px_rgba(20,25,28,0.04),0_24px_60px_-40px_rgba(20,25,28,0.3)]">
            <div className="mode-shift flex items-center justify-between gap-4 border-b border-hairline px-5 py-3.5">
              <span className="t-eyebrow mode-shift flex items-center gap-2.5 text-accent">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-accent"
                  aria-hidden="true"
                />
                Live network
              </span>
              <span className="t-mono text-ink-3">8 facilities</span>
            </div>

            <NetworkMesh
              className="h-[58vh] min-h-[360px] w-full"
              density={1.3}
            />

            <figcaption className="t-mono border-t border-hairline px-5 py-3.5 text-ink-3">
              Nodes are facilities. Edges are transfers as they happen.
            </figcaption>
          </figure>
        </Reveal>
      </div>
    </Section>
  );
}
