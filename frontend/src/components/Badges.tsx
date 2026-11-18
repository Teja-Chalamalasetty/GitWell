import type { ReactNode } from "react";
import type { DraftStatus, RuleStatus } from "../api/types";
import { GRADE_CLASSES, RULE_STATUS } from "../lib/status";

const BASE = "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold";

export function StatusBadge({ status }: { status: RuleStatus }) {
  const { label, classes } = RULE_STATUS[status];
  return (
    <span className={`${BASE} ${classes}`} data-status={status}>
      {label}
    </span>
  );
}

export function GradeBadge({ grade }: { grade: string | null | undefined }) {
  if (!grade) return <span className="text-zinc-400">—</span>;
  const classes = GRADE_CLASSES[grade] ?? GRADE_CLASSES.F;
  return (
    <span
      className={`${BASE} min-w-6 justify-center ${classes}`}
      data-grade={grade}
      aria-label={`Grade ${grade}`}
    >
      {grade}
    </span>
  );
}

export function Chip({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "warn" | "fail" | "info" | "success";
  title?: string;
}) {
  const tones = {
    neutral: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    warn: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    fail: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
    info: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
    success: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  };
  return (
    <span className={`${BASE} ${tones[tone]}`} title={title}>
      {children}
    </span>
  );
}

export function ArchivedBadge() {
  return <Chip title="This repository is archived on GitHub">Archived</Chip>;
}

/** Score + grade, with "Incomplete" / "Partial" labels (PRD 15). */
export function ScoreBadge({
  score,
  grade,
  rulesErrored = 0,
  isPartial = false,
}: {
  score: number | null | undefined;
  grade: string | null | undefined;
  rulesErrored?: number;
  isPartial?: boolean;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="text-base font-semibold tabular-nums">
        {score === null || score === undefined ? "—" : score}
      </span>
      <GradeBadge grade={grade} />
      {rulesErrored > 0 && (
        <Chip tone="warn" title={`${rulesErrored} rule(s) failed to run`}>
          Incomplete
        </Chip>
      )}
      {isPartial && (
        <Chip tone="info" title="Not every file could be read">
          Partial
        </Chip>
      )}
    </span>
  );
}

export function DeltaBadge({ delta }: { delta: number | null | undefined }) {
  if (delta === null || delta === undefined) return <span className="text-zinc-400">—</span>;
  if (delta === 0) return <span className="text-zinc-500">0</span>;
  const up = delta > 0;
  return (
    <span
      className={`font-medium tabular-nums ${up ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}
      aria-label={`${up ? "up" : "down"} ${Math.abs(delta)}`}
    >
      {up ? "▲" : "▼"} {Math.abs(delta)}
    </span>
  );
}

const DRAFT_STATUS: Record<DraftStatus, { label: string; tone: "neutral" | "warn" | "info" | "success" }> = {
  drafted: { label: "Draft", tone: "info" },
  approved: { label: "Approved, not posted", tone: "warn" },
  posting: { label: "Posting…", tone: "info" },
  posted: { label: "Posted", tone: "success" },
  discarded: { label: "Discarded", tone: "neutral" },
};

export function DraftStatusChip({ status }: { status: DraftStatus }) {
  const { label, tone } = DRAFT_STATUS[status];
  return (
    <span data-draft-status={status}>
      <Chip tone={tone}>{label}</Chip>
    </span>
  );
}
