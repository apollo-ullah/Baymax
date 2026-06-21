import { cn } from "@/lib/cn";

/**
 * Consistent section rhythm + container. Every landing section wraps its
 * content in this so vertical spacing and max-width stay uniform.
 */
export function Section({
  id,
  className,
  containerClassName,
  children,
}: {
  id?: string;
  className?: string;
  containerClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className={cn("scroll-mt-24 py-20 sm:py-28", className)}
    >
      <div
        className={cn(
          "mx-auto w-full max-w-[1240px] px-5 sm:px-8",
          containerClassName,
        )}
      >
        {children}
      </div>
    </section>
  );
}
