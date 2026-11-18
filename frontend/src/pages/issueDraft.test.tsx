import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { DraftStatus, IssueDraft, RepoSummary, Role } from "../api/types";
import { errorResponse, jsonResponse, mockApi, renderWithProviders } from "../test/utils";

vi.mock("../components/Charts", () => ({
  FailuresChart: () => <div />,
  TrendChart: () => <div />,
  ScoreHistoryChart: () => <div data-testid="history-chart" />,
  GradeBar: () => <div />,
}));

const NOW = "2026-11-18T12:00:00Z";

function draft(overrides: Partial<IssueDraft> = {}): IssueDraft {
  return {
    id: 5,
    scan_id: 50,
    repo_id: 1,
    repo_full_name: "o/r",
    repo_is_private: false,
    title: "GitWell: 2 quality check(s) need attention (score 40/100)",
    body: "## ❌ Failing\n- [ ] **Tests**: No test files found.\n  Add pytest.",
    status: "drafted",
    post_attempts: 0,
    github_issue_number: null,
    github_issue_url: null,
    last_error: null,
    created_by_id: 1,
    approved_by_id: null,
    created_at: NOW,
    updated_at: NOW,
    approved_at: null,
    ...overrides,
  };
}

function signIn(role: Role, extra: Record<string, () => unknown> = {}, tokenConfigured = true) {
  localStorage.setItem("gitwell.token", "tok");
  return mockApi({
    "GET /auth/me": () => ({ id: 1, email: `${role}@example.com`, role, is_active: true, created_at: NOW }),
    "GET /config": () => ({ allow_registration: false, github_token_configured: tokenConfigured, version: "1" }),
    ...extra,
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

async function open(id = 5) {
  renderWithProviders(<App />, { route: `/issue-drafts/${id}` });
}

describe("draft page: drafted (admin)", () => {
  it("shows editable title and body with a live preview", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft() });
    await open();
    const title = await screen.findByLabelText("Title");
    const body = screen.getByLabelText("Body (Markdown)");
    expect(title).toHaveValue(draft().title);
    expect(body).toHaveValue(draft().body);
    // the preview renders the markdown
    const preview = screen.getByLabelText("Preview");
    expect(within(preview).getByText("Tests").tagName).toBe("STRONG");
    expect(screen.getByRole("status")).toBeInTheDocument(); // the "nothing has been posted" banner

    await userEvent.clear(body);
    await userEvent.type(body, "Hello **bold** text");
    expect(within(preview).getByText("bold").tagName).toBe("STRONG");
    await userEvent.clear(title);
    await userEvent.type(title, "My title");
    expect(within(preview).getByText("My title")).toBeInTheDocument();
  });

  it("the preview never turns raw HTML into elements", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft() });
    await open();
    const body = await screen.findByLabelText("Body (Markdown)");
    await userEvent.clear(body);
    await userEvent.type(body, "<img src=x onerror=alert(1)> and [x](javascript:alert(1))");
    const preview = screen.getByLabelText("Preview");
    expect(preview.querySelector("img")).toBeNull();
    expect(preview.querySelector("a")?.getAttribute("href") ?? "").toBe("");
  });

  it("Save is disabled until something changes, then sends only the changed fields", async () => {
    let current = draft();
    const { calls } = signIn("admin", {
      "GET /issue-drafts/5": () => current,
      "PATCH /issue-drafts/5": () => {
        current = draft({ title: "Edited", updated_at: "2026-11-18T12:05:00Z" });
        return current;
      },
    });
    await open();
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Title"), "{Control>}a{/Control}Edited");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ title: "Edited" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("validates before saving", async () => {
    const { calls } = signIn("admin", { "GET /issue-drafts/5": () => draft() });
    await open();
    const title = await screen.findByLabelText("Title");
    await userEvent.clear(title);
    expect(screen.getByText("The title cannot be empty.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(title, "ok");
    const body = screen.getByLabelText("Body (Markdown)");
    await userEvent.clear(body);
    expect(screen.getByText("The body cannot be empty.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("shows the server's error when saving fails", async () => {
    signIn("admin", {
      "GET /issue-drafts/5": () => draft(),
      "PATCH /issue-drafts/5": () => errorResponse(409, "INVALID_STATE", "This draft is approved and cannot be changed that way."),
    });
    await open();
    await userEvent.type(await screen.findByLabelText("Title"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/cannot be changed that way/)).toBeInTheDocument();
  });

  it("will not post unsaved edits: Approve is disabled until they are saved", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft() });
    await open();
    const approve = await screen.findByRole("button", { name: "Approve & post" });
    await waitFor(() => expect(approve).toBeEnabled()); // once the server config has loaded
    await userEvent.type(screen.getByLabelText("Title"), "!");
    expect(approve).toBeDisabled();
    expect(screen.getByText("Save your changes before posting.")).toBeInTheDocument();
  });

  it("disables posting when no GitHub token is configured", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft() }, false);
    await open();
    expect(await screen.findByRole("button", { name: "Approve & post" })).toBeDisabled();
    expect(await screen.findByText(/no GitHub token is configured/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve & post" })).toBeDisabled();
  });
});

describe("draft page: approving", () => {
  it.each([
    [false, "public"],
    [true, "private"],
  ])("the confirmation says whether the issue is public or private (private=%s)", async (isPrivate, word) => {
    const { calls } = signIn("admin", { "GET /issue-drafts/5": () => draft({ repo_is_private: isPrivate }) });
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Approve & post" }));
    const dialog = screen.getByRole("dialog", { name: "Post this issue?" });
    expect(within(dialog).getByText(word).tagName).toBe("STRONG");
    expect(dialog).toHaveTextContent(`This will create a ${word} issue on o/r`);
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false); // nothing happens yet

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
  });

  it("confirming posts once and shows the posted, read-only state with a link", async () => {
    let status: DraftStatus = "drafted";
    const { calls } = signIn("admin", {
      "GET /issue-drafts/5": () =>
        status === "posted"
          ? draft({ status, github_issue_number: 12, github_issue_url: "https://github.com/o/r/issues/12", post_attempts: 1 })
          : draft(),
      "POST /issue-drafts/5/approve": () => {
        status = "posted";
        return draft({ status, github_issue_number: 12, github_issue_url: "https://github.com/o/r/issues/12", post_attempts: 1 });
      },
    });
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Approve & post" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Post to GitHub" }));

    const link = await screen.findByRole("link", { name: /Open issue #12 on GitHub/ });
    expect(link).toHaveAttribute("href", "https://github.com/o/r/issues/12");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(calls.filter((c) => c.path === "/issue-drafts/5/approve")).toHaveLength(1);
    // read-only now: no inputs, no action buttons
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Retry|Discard|Save/ })).not.toBeInTheDocument();
  });

  it("refetches the draft after a failed post and shows the failure", async () => {
    let state: "drafted" | "approved" = "drafted";
    const { calls } = signIn("admin", {
      "GET /issue-drafts/5": () =>
        state === "approved"
          ? draft({ status: "approved", post_attempts: 1, last_error: "GitHub is unavailable or timed out." })
          : draft(),
      "POST /issue-drafts/5/approve": () => {
        state = "approved";
        return errorResponse(503, "GITHUB_UNAVAILABLE", "GitHub is unavailable or timed out.");
      },
    });
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Approve & post" }));
    const getsBefore = calls.filter((c) => c.method === "GET" && c.path === "/issue-drafts/5").length;
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Post to GitHub" }));

    // the draft is refetched after the failed post...
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.path === "/issue-drafts/5").length).toBeGreaterThan(getsBefore),
    );
    // ...and the page moves to the retryable `approved` state, showing why
    const banner = await screen.findByText(/approved but not posted/);
    expect(banner.closest("[role=status]")).toHaveTextContent("GitHub is unavailable or timed out.");
    expect(screen.getByRole("button", { name: "Retry posting" })).toBeInTheDocument();
  });

  it("an approved draft shows the error with Retry posting and Discard", async () => {
    const { calls } = signIn("admin", {
      "GET /issue-drafts/5": () => draft({ status: "approved", post_attempts: 1, last_error: "The GitHub token is not allowed to create issues in this repository." }),
      "POST /issue-drafts/5/approve": () => draft({ status: "posted", github_issue_number: 3, github_issue_url: "https://github.com/o/r/issues/3" }),
    });
    await open();
    expect(await screen.findByText(/not allowed to create issues/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument(); // no longer editable
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry posting" }));
    const dialog = screen.getByRole("dialog", { name: "Retry posting?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Post to GitHub" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/issue-drafts/5/approve")).toBe(true));
  });

  it("discard asks for confirmation first", async () => {
    const { calls } = signIn("admin", {
      "GET /issue-drafts/5": () => draft(),
      "POST /issue-drafts/5/discard": () => draft({ status: "discarded" }),
    });
    await open();
    await userEvent.click(await screen.findByRole("button", { name: "Discard" }));
    const dialog = screen.getByRole("dialog", { name: "Discard this draft?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.path.endsWith("/discard"))).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/issue-drafts/5/discard")).toBe(true));
  });
});

describe("draft page: other states and roles", () => {
  it("posting is read-only with a progress banner", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft({ status: "posting", post_attempts: 1 }) });
    await open();
    expect(await screen.findByText(/Posting to GitHub/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Retry/ })).not.toBeInTheDocument();
  });

  it("a discarded draft is read-only", async () => {
    signIn("admin", { "GET /issue-drafts/5": () => draft({ status: "discarded" }) });
    await open();
    expect(await screen.findByText(/This draft was discarded/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Discard|Save/ })).not.toBeInTheDocument();
  });

  it("never links to a non-GitHub issue URL", async () => {
    signIn("admin", {
      "GET /issue-drafts/5": () => draft({ status: "posted", github_issue_number: 1, github_issue_url: "javascript:alert(1)" }),
    });
    await open();
    expect(await screen.findByText(/The issue was created on GitHub/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Open issue/ })).not.toBeInTheDocument();
  });

  it("viewers see the draft read-only, with no inputs or action buttons", async () => {
    signIn("viewer", { "GET /issue-drafts/5": () => draft() });
    await open();
    expect(await screen.findByText(draft().title)).toBeInTheDocument();
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Discard|Save|Retry/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Only administrators can edit or post drafts/)).toBeInTheDocument();
    expect(screen.getByText("Tests").tagName).toBe("STRONG"); // body rendered as markdown
  });

  it("shows an error for an unknown draft", async () => {
    signIn("admin", { "GET /issue-drafts/9": () => errorResponse(404, "NOT_FOUND", "Issue draft not found.") });
    await open(9);
    expect(await screen.findByText("Issue draft not found.")).toBeInTheDocument();
  });

  it("rejects a malformed id", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/issue-drafts/abc" });
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

// ---- the repo page: draft button and list ---------------------------------------------------------

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
    added_at: NOW,
    latest_scan: { id: 50, status: "completed", error_code: null, created_at: NOW, finished_at: NOW },
    latest_completed_scan: {
      id: 50, score: 40, grade: "D", commit_sha: "abcdef1234", finished_at: NOW,
      rules_errored: 0, is_partial: false, has_findings: true,
    },
    score_delta: null,
    active_scan_id: null,
    open_draft_id: null,
    ...overrides,
  };
}

function repoPage(role: Role, repoData: RepoSummary, drafts: IssueDraft[] = [], extra: Record<string, () => unknown> = {}) {
  return signIn(role, {
    "GET /repos/1": () => repoData,
    "GET /repos/1/scans": () => ({ items: [], total: 0 }),
    "GET /scans/50": () => ({ id: 50, repo_id: 1, status: "completed", results: [], is_partial: false, rules_errored: 0 }),
    "GET /issue-drafts": () => ({ items: drafts, total: drafts.length }),
    ...extra,
  });
}

describe("repo page: GitHub issues", () => {
  it("offers the button to admins when the scan has findings", async () => {
    repoPage("admin", repo());
    renderWithProviders(<App />, { route: "/repos/1" });
    expect(await screen.findByRole("button", { name: "Draft GitHub issue" })).toBeEnabled();
  });

  it.each([
    ["archived", { is_archived: true }, "This repository is archived on GitHub."],
    ["issues disabled", { has_issues: false }, "Issues are disabled for this repository."],
    ["no completed scan", { latest_completed_scan: null }, "Run a scan first."],
  ])("disables the button and says why when %s", async (_label, overrides, reason) => {
    repoPage("admin", repo(overrides as Partial<RepoSummary>));
    renderWithProviders(<App />, { route: "/repos/1" });
    const button = await screen.findByRole("button", { name: "Draft GitHub issue" });
    expect(button).toBeDisabled();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(button.closest("span")).toHaveAttribute("title", reason);
  });

  it("disables the button when the latest scan has nothing to report", async () => {
    const base = repo();
    repoPage("admin", repo({ latest_completed_scan: { ...(base.latest_completed_scan as NonNullable<RepoSummary["latest_completed_scan"]>), has_findings: false } }));
    renderWithProviders(<App />, { route: "/repos/1" });
    expect(await screen.findByRole("button", { name: "Draft GitHub issue" })).toBeDisabled();
    expect(screen.getByText("The latest scan has nothing to report.")).toBeInTheDocument();
  });

  it("links to the open draft instead of showing the button", async () => {
    repoPage("admin", repo({ open_draft_id: 5 }), [draft()]);
    renderWithProviders(<App />, { route: "/repos/1" });
    const link = await screen.findByRole("link", { name: "Open the issue draft" });
    expect(link).toHaveAttribute("href", "/issue-drafts/5");
    expect(screen.queryByRole("button", { name: "Draft GitHub issue" })).not.toBeInTheDocument();
  });

  it("creates a draft from the latest completed scan and opens it", async () => {
    const { calls } = repoPage("admin", repo(), [], {
      "POST /scans/50/issue-draft": () => jsonResponse(draft({ id: 8 }), 201),
      "GET /issue-drafts/8": () => draft({ id: 8 }),
    });
    renderWithProviders(<App />, { route: "/repos/1" });
    await userEvent.click(await screen.findByRole("button", { name: "Draft GitHub issue" }));
    expect(await screen.findByRole("heading", { name: "Issue draft" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/scans/50/issue-draft")).toBe(true);
  });

  it("shows the server's reason when drafting is refused", async () => {
    repoPage("admin", repo(), [], {
      "POST /scans/50/issue-draft": () => errorResponse(409, "DRAFT_EXISTS", "This repository already has an open issue draft."),
    });
    renderWithProviders(<App />, { route: "/repos/1" });
    await userEvent.click(await screen.findByRole("button", { name: "Draft GitHub issue" }));
    expect(await screen.findByText("This repository already has an open issue draft.")).toBeInTheDocument();
  });

  it("lists this repo's drafts with status chips and issue links", async () => {
    repoPage("viewer", repo(), [
      draft({ id: 7, status: "posted", title: "Posted one", github_issue_number: 12, github_issue_url: "https://github.com/o/r/issues/12" }),
      draft({ id: 6, status: "discarded", title: "Old one" }),
    ]);
    renderWithProviders(<App />, { route: "/repos/1" });
    expect(await screen.findByRole("link", { name: "Posted one" })).toHaveAttribute("href", "/issue-drafts/7");
    expect(screen.getByRole("link", { name: "#12 ↗" })).toHaveAttribute("href", "https://github.com/o/r/issues/12");
    expect(screen.getByText("Posted")).toBeInTheDocument();
    expect(screen.getByText("Discarded")).toBeInTheDocument();
  });

  it("viewers see the list but no draft button", async () => {
    repoPage("viewer", repo(), [draft()]);
    renderWithProviders(<App />, { route: "/repos/1" });
    await screen.findByRole("link", { name: draft().title });
    expect(screen.queryByRole("button", { name: "Draft GitHub issue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the issue draft" })).not.toBeInTheDocument();
  });

  it("shows an empty state when there are no drafts", async () => {
    repoPage("admin", repo());
    renderWithProviders(<App />, { route: "/repos/1" });
    expect(await screen.findByText("No issue drafts yet")).toBeInTheDocument();
  });
});
