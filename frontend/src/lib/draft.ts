import type { RepoSummary } from "../api/types";

export type DraftButtonState =
  | { kind: "link"; draftId: number }
  | { kind: "button"; disabled: false; scanId: number }
  | { kind: "button"; disabled: true; reason: string };

/**
 * PRD 15: "Draft GitHub issue" is enabled only when the latest completed scan has findings, the
 * repo has no open draft, and it is neither archived nor without issues. Otherwise it is disabled
 * with the reason, or replaced by a link to the open draft.
 */
export function draftButtonState(repo: RepoSummary): DraftButtonState {
  if (repo.open_draft_id !== null) return { kind: "link", draftId: repo.open_draft_id };
  const done = repo.latest_completed_scan;
  if (!done) return { kind: "button", disabled: true, reason: "Run a scan first." };
  if (repo.is_archived) {
    return { kind: "button", disabled: true, reason: "This repository is archived on GitHub." };
  }
  if (!repo.has_issues) {
    return { kind: "button", disabled: true, reason: "Issues are disabled for this repository." };
  }
  if (!done.has_findings) {
    return { kind: "button", disabled: true, reason: "The latest scan has nothing to report." };
  }
  return { kind: "button", disabled: false, scanId: done.id };
}

/** Only link to an issue URL that really points at github.com. */
export function safeGithubUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "github.com" ? parsed.href : null;
  } catch {
    return null;
  }
}
