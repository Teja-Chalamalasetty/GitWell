import { useSyncExternalStore } from "react";

const QUERY = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(QUERY).matches;
}

/** Follows the system theme (there is no toggle). Charts need explicit colours. */
export function useDarkMode(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

export function chartColors(dark: boolean) {
  return {
    text: dark ? "#a1a1aa" : "#52525b",
    grid: dark ? "#3f3f46" : "#e4e4e7",
    line: "#3b82f6",
    fail: "#ef4444",
    warn: "#f59e0b",
    tooltipBg: dark ? "#18181b" : "#ffffff",
    tooltipBorder: dark ? "#3f3f46" : "#e4e4e7",
  };
}
