import { describe, expect, it } from "vitest";
import type { RepoSummary } from "../api/types";
import { draftButtonState, safeGithubUrl } from "./draft";

function repo(overrides: Partial<RepoSummary> = {}): RepoSummary {
  return {
    id: 1,
    github_id: 1,
    url: "https://github.com/o/r",
    full_name: "o/r",
    default_branch: "main",
    is_private: false,
    is_archived: false,
    has_issues: true,
    added_at: "2026-11-01T00:00:00Z",
    latest_scan: null,
    latest_completed_scan: {
      id: 9,
      score: 40,
      grade: "D",
      commit_sha: "abc",
      finished_at: null,
      rules_errored: 0,
      is_partial: false,
      has_findings: true,
    },
    score_delta: null,
    active_scan_id: null,
    open_draft_id: null,
    ...overrides,
  };
}

describe("draftButtonState", () => {
  it("is enabled when the latest scan has findings and nothing blocks it", () => {
    expect(draftButtonState(repo())).toEqual({ kind: "button", disabled: false, scanId: 9 });
  });

  it("links to the open draft instead of offering a new one", () => {
    expect(draftButtonState(repo({ open_draft_id: 4 }))).toEqual({ kind: "link", draftId: 4 });
  });

  it("the open draft wins over every other reason", () => {
    const state = draftButtonState(repo({ open_draft_id: 4, is_archived: true, has_issues: false }));
    expect(state).toEqual({ kind: "link", draftId: 4 });
  });

  it.each([
    ["no completed scan", { latest_completed_scan: null }, "Run a scan first."],
    ["archived", { is_archived: true }, "This repository is archived on GitHub."],
    ["issues disabled", { has_issues: false }, "Issues are disabled for this repository."],
  ])("is disabled when %s", (_label, overrides, reason) => {
    expect(draftButtonState(repo(overrides as Partial<RepoSummary>))).toEqual({
      kind: "button",
      disabled: true,
      reason,
    });
  });

  it("is disabled when the latest scan has nothing to report", () => {
    const base = repo();
    const clean = repo({
      latest_completed_scan: { ...(base.latest_completed_scan as NonNullable<RepoSummary["latest_completed_scan"]>), has_findings: false },
    });
    expect(draftButtonState(clean)).toEqual({
      kind: "button",
      disabled: true,
      reason: "The latest scan has nothing to report.",
    });
  });
});

describe("safeGithubUrl", () => {
  it.each([
    ["https://github.com/o/r/issues/5", "https://github.com/o/r/issues/5"],
    ["https://github.com/o/r/issues/5#issue-1", "https://github.com/o/r/issues/5#issue-1"],
  ])("accepts %s", (input, expected) => {
    expect(safeGithubUrl(input)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    "",
    "javascript:alert(1)",
    "http://github.com/o/r/issues/5",
    "https://evil.example/o/r",
    "https://github.com.evil.example/o/r",
    "https://notgithub.com/o/r",
    "//github.com/o/r",
    "not a url",
  ])("rejects %j", (input) => {
    expect(safeGithubUrl(input)).toBeNull();
  });
});
