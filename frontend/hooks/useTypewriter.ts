"use client";

import { useEffect, useMemo, useState } from "react";

// Which items have already finished revealing (module-level so a re-render or
// poll doesn't restart the animation for the same message).
const revealed = new Set<string>();

/** Progressively reveal `full` a few lines at a time, like it's being written.
 * `animate` gates it (e.g. only for freshly-arrived messages); when false, or
 * once an id has finished, the full text shows immediately. */
export function useTypewriter(full: string, id: string, animate: boolean) {
  const lines = useMemo(() => full.split("\n"), [full]);
  const skip = !animate || revealed.has(id) || lines.length === 0;
  const [shown, setShown] = useState(skip ? lines.length : 0);

  useEffect(() => {
    if (skip) {
      setShown(lines.length);
      return;
    }
    setShown(0);
    // Scale the step so any length finishes in ~5s, never slower than 1 line/tick.
    const perTick = Math.max(1, Math.ceil(lines.length / 120));
    let n = 0;
    const timer = setInterval(() => {
      n += perTick;
      if (n >= lines.length) {
        setShown(lines.length);
        revealed.add(id);
        clearInterval(timer);
      } else {
        setShown(n);
      }
    }, 45);
    return () => clearInterval(timer);
  }, [id, lines.length, skip]);

  return { text: lines.slice(0, shown).join("\n"), done: shown >= lines.length };
}
