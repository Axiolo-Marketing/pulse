import { useEffect, useState } from "react";

/** `matchMedia` where available; environments without it (SSR, jsdom, very
 * old browsers) get `null` and are treated as not matching — i.e. the phone
 * layout, which works everywhere. */
function mediaQueryList(query: string): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia(query);
}

/** Live `matchMedia` result — re-renders when it flips (window resize,
 * rotating a tablet, docking a keyboard/mouse). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => mediaQueryList(query)?.matches ?? false,
  );
  useEffect(() => {
    const mql = mediaQueryList(query);
    if (!mql) return;
    const onChange = (): void => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Desktop-class layout: matches Tailwind's `lg` breakpoint. */
export const DESKTOP_QUERY = "(min-width: 1024px)";
/** Room for a reference document beside the question: Tailwind's `xl`. */
export const WIDE_QUERY = "(min-width: 1280px)";
/** A mouse/trackpad (not touch) — used to show keyboard hints. */
export const FINE_POINTER_QUERY = "(hover: hover) and (pointer: fine)";
