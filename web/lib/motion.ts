import type { Variants, Transition } from "motion/react";

/**
 * Motion language — three custom curves, reused everywhere.
 *   outSoft — the house reveal/settle curve (soft expo-out)
 *   calm    — symmetric ease for environmental state transitions (calm⇄crisis)
 *   press   — slight overshoot for the "friendly caretaker-robot" button feel
 *
 * Durations are deliberately few. Respect prefers-reduced-motion at the call
 * site (see `useReducedMotion` from motion/react) — these are the *intended*
 * values when motion is allowed.
 */

export const ease = {
  outSoft: [0.22, 1, 0.36, 1],
  calm: [0.65, 0, 0.35, 1],
  press: [0.34, 1.56, 0.64, 1],
} as const;

export const dur = {
  micro: 0.18,
  base: 0.42,
  slow: 0.9,
  shift: 0.7,
} as const;

/** Single element easing into place. */
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 22 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: dur.base, ease: ease.outSoft },
  },
};

/** Container that staggers its children's reveals. */
export const stagger = (gap = 0.08, delay = 0): Variants => ({
  hidden: {},
  show: {
    transition: { staggerChildren: gap, delayChildren: delay },
  },
});

/** Soft-press interaction for buttons / magnetic targets. */
export const press: { whileHover: object; whileTap: object } = {
  whileHover: { scale: 1.025, transition: { duration: dur.micro, ease: ease.press } },
  whileTap: { scale: 0.97, transition: { duration: 0.1, ease: ease.press } },
};

/** Shared viewport config so scroll-reveals trigger consistently. */
export const inView = { once: true, amount: 0.35 } as const;

export const shiftTransition: Transition = { duration: dur.shift, ease: ease.calm };
