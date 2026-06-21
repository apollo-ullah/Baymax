"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type Mode = "calm" | "crisis";

interface CrisisModeValue {
  mode: Mode;
  isCrisis: boolean;
  setMode: (m: Mode) => void;
  toggle: () => void;
}

const CrisisModeContext = createContext<CrisisModeValue | null>(null);

/**
 * The signature state. Reflects calm⇄crisis onto `document.documentElement`
 * as `data-mode`, which the token system reads to warm the entire palette
 * (canvas, accents, glows, the mesh) in one eased transition.
 */
export function CrisisModeProvider({
  children,
  initial = "calm",
}: {
  children: React.ReactNode;
  initial?: Mode;
}) {
  const [mode, setModeState] = useState<Mode>(initial);

  // Deep-link / demo support: ?mode=crisis (or calm) sets the initial state.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("mode");
    if (p === "crisis" || p === "calm") setModeState(p);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.mode = mode;
  }, [mode]);

  const setMode = useCallback((m: Mode) => setModeState(m), []);
  const toggle = useCallback(
    () => setModeState((m) => (m === "calm" ? "crisis" : "calm")),
    [],
  );

  const value = useMemo<CrisisModeValue>(
    () => ({ mode, isCrisis: mode === "crisis", setMode, toggle }),
    [mode, setMode, toggle],
  );

  return (
    <CrisisModeContext.Provider value={value}>
      {children}
    </CrisisModeContext.Provider>
  );
}

export function useCrisisMode(): CrisisModeValue {
  const ctx = useContext(CrisisModeContext);
  if (!ctx) {
    throw new Error("useCrisisMode must be used within a CrisisModeProvider");
  }
  return ctx;
}
