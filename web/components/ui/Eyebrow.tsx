import { cn } from "@/lib/cn";

/**
 * Section label. The accent dot + uppercase mono reads as a clinical marker.
 * Pass `index` only where the content is a real ordered sequence.
 */
export function Eyebrow({
  children,
  index,
  className,
}: {
  children: React.ReactNode;
  index?: string;
  className?: string;
}) {
  return (
    <div className={cn("mode-shift flex items-center gap-3 text-accent", className)}>
      {index != null && <span className="t-mono text-ink-3">{index}</span>}
      <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
      <span className="t-eyebrow">{children}</span>
    </div>
  );
}
