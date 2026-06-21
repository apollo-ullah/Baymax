"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { ease, dur } from "@/lib/motion";
import { cn } from "@/lib/cn";

type Variant = "primary" | "ghost";
type Size = "md" | "lg";

const base =
  "mode-shift inline-flex items-center justify-center gap-2 rounded-pill font-medium " +
  "select-none whitespace-nowrap focus-visible:outline-2";

const sizes: Record<Size, string> = {
  md: "h-11 px-5 text-[0.95rem]",
  lg: "h-14 px-7 text-[1.02rem]",
};

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-on-accent shadow-[0_1px_2px_rgba(20,25,28,0.12),0_12px_30px_-12px_rgba(var(--glow),0.55)] " +
    "hover:shadow-[0_2px_4px_rgba(20,25,28,0.14),0_18px_40px_-14px_rgba(var(--glow),0.7)]",
  ghost:
    "bg-transparent text-ink border border-hairline-strong hover:bg-paper-sunken",
};

interface CommonProps {
  variant?: Variant;
  size?: Size;
  className?: string;
  children: React.ReactNode;
}

type ButtonProps = CommonProps &
  Omit<React.ComponentProps<typeof motion.button>, "children"> & {
    href?: undefined;
  };

type LinkProps = CommonProps & {
  href: string;
};

export function Button(props: ButtonProps | LinkProps) {
  const { variant = "primary", size = "md", className, children } = props;
  const reduce = useReducedMotion();
  const classes = cn(base, sizes[size], variants[variant], className);

  const interaction = reduce
    ? {}
    : {
        whileHover: { scale: 1.025 },
        whileTap: { scale: 0.97 },
        transition: { duration: dur.micro, ease: ease.press },
      };

  if ("href" in props && props.href) {
    return (
      <motion.div {...interaction} className="inline-flex">
        <Link href={props.href} className={classes}>
          {children}
        </Link>
      </motion.div>
    );
  }

  const { variant: _v, size: _s, className: _c, children: _ch, ...rest } =
    props as ButtonProps;
  void _v;
  void _s;
  void _c;
  void _ch;
  return (
    <motion.button {...interaction} className={classes} {...rest}>
      {children}
    </motion.button>
  );
}
