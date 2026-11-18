import type { RuleStatus, ScanStatus } from "../api/types";

// Status colours (PRD 15): pass green, warn amber, fail red, N/A grey, error purple.
export const RULE_STATUS: Record<RuleStatus, { label: string; classes: string }> = {
  pass: {
    label: "Pass",
    classes: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  },
  warn: {
    label: "Warning",
    classes: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  },
  fail: {
    label: "Fail",
    classes: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  },
  not_applicable: {
    label: "N/A",
    classes: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  },
  error: {
    label: "Error",
    classes: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  },
};

export const GRADE_CLASSES: Record<string, string> = {
  A: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  B: "bg-lime-100 text-lime-800 dark:bg-lime-900/40 dark:text-lime-300",
  C: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  D: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  F: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};

export const GRADES = ["A", "B", "C", "D", "F"] as const;

// Solid colours for charts and the distribution bar (same hue order as the badges).
export const GRADE_COLORS: Record<string, string> = {
  A: "#16a34a",
  B: "#84cc16",
  C: "#eab308",
  D: "#f97316",
  F: "#dc2626",
};

export const SCAN_STATUS_LABEL: Record<ScanStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

export function isActive(status: ScanStatus | undefined | null): boolean {
  return status === "queued" || status === "running";
}

export function isTerminal(status: ScanStatus | undefined | null): boolean {
  return status === "completed" || status === "failed";
}
