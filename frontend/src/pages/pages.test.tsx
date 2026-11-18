import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { DashboardSummary, RepoSummary, Role, User } from "../api/types";
import { errorResponse, jsonResponse, mockApi, renderWithProviders } from "../test/utils";

// Recharts needs real layout sizes, which jsdom does not have; the charts are tested by eye.
vi.mock("../components/Charts", () => ({
  FailuresChart: () => <div data-testid="failures-chart" />,
  TrendChart: () => <div data-testid="trend-chart" />,
  ScoreHistoryChart: () => <div data-testid="history-chart" />,
  GradeBar: () => <div data-testid="grade-bar" />,
}));

const user = (role: Role, overrides: Partial<User> = {}): User => ({
  id: 1,
  email: `${role}@example.com`,
  role,
  is_active: true,
  created_at: "2026-11-01T00:00:00Z",
  ...overrides,
});

const summary: DashboardSummary = {
  repo_count: 2,
  scanned_repo_count: 2,
  average_score: 61.5,
  grade_distribution: { A: 0, B: 0, C: 1, D: 0, F: 1 },
  rule_failures: [{ rule_id: "tests", rule_name: "Tests", fail: 1, warn: 0 }],
  score_trend: [{ date: "2026-11-18", average_score: 61.5 }],
};

function repo(id: number, overrides: Partial<RepoSummary> = {}): RepoSummary {
  return {
    id,
    github_id: id,
    url: `https://github.com/o/r${id}`,
    full_name: `o/r${id}`,
    default_branch: "main",
    is_private: false,
    is_archived: false,
    has_issues: true,
    added_at: "2026-11-01T00:00:00Z",
    latest_scan: {
      id: id * 10,
      status: "completed",
      error_code: null,
      created_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
      finished_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
    },
    latest_completed_scan: {
      id: id * 10,
      score: 82,
      grade: "B",
      commit_sha: "abcdef1234",
      finished_at: new Date().toISOString(),
      rules_errored: 0,
      is_partial: false,
      has_findings: true,
    },
    score_delta: 7,
    active_scan_id: null,
    open_draft_id: null,
    ...overrides,
  };
}

function signIn(role: Role, extra: Record<string, () => unknown> = {}, repos: RepoSummary[] = [repo(1)]) {
  localStorage.setItem("gitwell.token", "tok");
  return mockApi({
    "GET /auth/me": () => user(role),
    "GET /config": () => ({ allow_registration: false, github_token_configured: true, version: "1.0.0" }),
    "GET /dashboard/summary": () => summary,
    "GET /repos": () => ({ items: repos, total: repos.length }),
    ...extra,
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("route protection", () => {
  it("sends anonymous visitors to the login page", async () => {
    mockApi({ "GET /config": () => ({ allow_registration: false }) });
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByRole("heading", { name: "GitWell" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("keeps viewers out of the users page", async () => {
    signIn("viewer");
    renderWithProviders(<App />, { route: "/users" });
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("shows admins the users link", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByRole("link", { name: "Users" })).toBeInTheDocument();
  });

  it("clears the session when the server answers 401", async () => {
    localStorage.setItem("gitwell.token", "stale");
    mockApi({
      "GET /auth/me": () => errorResponse(401, "UNAUTHENTICATED", "Invalid or expired token."),
      "GET /config": () => ({ allow_registration: false }),
    });
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(localStorage.getItem("gitwell.token")).toBeNull();
  });

  it("shows a not-found page for unknown routes", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/nope" });
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

describe("login page", () => {
  it("shows an inline error for a wrong password and stays on the page", async () => {
    mockApi({
      "GET /config": () => ({ allow_registration: false }),
      "POST /auth/login": () => errorResponse(401, "INVALID_CREDENTIALS", "Invalid email or password."),
    });
    renderWithProviders(<App />, { route: "/login" });
    await userEvent.type(await screen.findByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(localStorage.getItem("gitwell.token")).toBeNull();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("explains rate limiting", async () => {
    mockApi({
      "GET /config": () => ({ allow_registration: false }),
      "POST /auth/login": () => errorResponse(429, "TOO_MANY_ATTEMPTS", "Too many attempts."),
    });
    renderWithProviders(<App />, { route: "/login" });
    await userEvent.type(await screen.findByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many attempts/i);
  });

  it("does not call the server when fields are empty", async () => {
    const { calls } = mockApi({ "GET /config": () => ({ allow_registration: false }) });
    renderWithProviders(<App />, { route: "/login" });
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your email and password.");
    expect(calls.some((c) => c.path === "/auth/login")).toBe(false);
  });

  it("signs in, stores the token and lands on the dashboard", async () => {
    const { calls } = mockApi({
      "GET /config": () => ({ allow_registration: false }),
      "POST /auth/login": () =>
        jsonResponse({ access_token: "test-new-token", token_type: "bearer", expires_in: 3600, user: user("admin") }),
      "GET /dashboard/summary": () => summary,
      "GET /repos": () => ({ items: [], total: 0 }),
    });
    renderWithProviders(<App />, { route: "/login" });
    await userEvent.type(await screen.findByLabelText("Email"), "admin@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "test-correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(localStorage.getItem("gitwell.token")).toBe("test-new-token");
    expect(calls.find((c) => c.path === "/auth/login")?.body).toEqual({
      email: "admin@example.com",
      password: "test-correct-horse-1",
    });
  });

  it("links to registration only when the server allows it", async () => {
    mockApi({ "GET /config": () => ({ allow_registration: false }) });
    const first = renderWithProviders(<App />, { route: "/login" });
    await screen.findByLabelText("Email");
    expect(screen.queryByRole("link", { name: "Register" })).not.toBeInTheDocument();
    first.unmount();

    mockApi({ "GET /config": () => ({ allow_registration: true }) });
    renderWithProviders(<App />, { route: "/login" });
    expect(await screen.findByRole("link", { name: "Register" })).toBeInTheDocument();
  });

  it("the register page redirects to login when registration is off", async () => {
    mockApi({ "GET /config": () => ({ allow_registration: false }) });
    renderWithProviders(<App />, { route: "/register" });
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("registering sends the user to sign in (no auto-login)", async () => {
    const { calls } = mockApi({
      "GET /config": () => ({ allow_registration: true }),
      "POST /auth/register": () => jsonResponse(user("viewer"), 201),
    });
    renderWithProviders(<App />, { route: "/register" });
    await userEvent.type(await screen.findByLabelText("Email"), "new@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "test-long-enough-pw");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Account created. Please sign in.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/register")).toBeDefined();
    expect(localStorage.getItem("gitwell.token")).toBeNull();
  });
});

describe("dashboard", () => {
  it("shows stats, charts and the repository table", async () => {
    signIn("viewer", {}, [repo(1), repo(2, { full_name: "o/second" })]);
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByText("o/second")).toBeInTheDocument();
    expect(screen.getByText("Repositories tracked")).toBeInTheDocument();
    expect(screen.getByText("61.5")).toBeInTheDocument();
    expect(screen.getByTestId("failures-chart")).toBeInTheDocument();
    expect(screen.getByTestId("trend-chart")).toBeInTheDocument();
    expect(screen.getByTestId("grade-bar")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Grade B")).toHaveLength(2);
    expect(screen.getAllByLabelText("up 7")).toHaveLength(2);
  });

  it("hides every admin control from viewers", async () => {
    signIn("viewer");
    renderWithProviders(<App />, { route: "/" });
    await screen.findByText("o/r1");
    expect(screen.queryByRole("button", { name: "Add repository" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Scan o\/r1 now/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete o\/r1/ })).not.toBeInTheDocument();
  });

  it("shows admins the add, scan and delete controls", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/" });
    await screen.findByText("o/r1");
    expect(screen.getByRole("button", { name: "Add repository" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scan o/r1 now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete o/r1" })).toBeInTheDocument();
  });

  it("shows status chips, badges and labels per row", async () => {
    signIn("viewer", {}, [
      repo(1, { active_scan_id: 11, is_archived: true }),
      repo(2, {
        latest_scan: { id: 5, status: "failed", error_code: "REPO_EMPTY", created_at: "2026-11-17T00:00:00Z", finished_at: null },
      }),
      repo(3, {
        latest_completed_scan: {
          id: 30, score: null, grade: null, commit_sha: null, finished_at: null,
          rules_errored: 1, is_partial: true, has_findings: false,
        },
        score_delta: null,
      }),
      repo(4, { latest_scan: null, latest_completed_scan: null, score_delta: -3 }),
    ]);
    renderWithProviders(<App />, { route: "/" });
    await screen.findByText("o/r1");
    expect(screen.getByText("Scanning…")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getByText("Last scan failed: REPO_EMPTY")).toBeInTheDocument();
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByLabelText("down 3")).toBeInTheDocument();
    expect(screen.getByText("Not scanned")).toBeInTheDocument();
  });

  it("requests a new sort when a column header is clicked, and toggles direction", async () => {
    const { calls } = signIn("viewer");
    renderWithProviders(<App />, { route: "/" });
    await screen.findByText("o/r1");
    const reposCalls = () => calls.filter((c) => c.path === "/repos");
    expect(reposCalls().at(-1)?.query.get("sort")).toBe("name");
    expect(reposCalls().at(-1)?.query.get("order")).toBe("asc");

    await userEvent.click(screen.getByRole("button", { name: /Score/ }));
    await waitFor(() => expect(reposCalls().at(-1)?.query.get("sort")).toBe("score"));
    expect(reposCalls().at(-1)?.query.get("order")).toBe("desc");

    await userEvent.click(screen.getByRole("button", { name: /Score/ }));
    await waitFor(() => expect(reposCalls().at(-1)?.query.get("order")).toBe("asc"));

    await userEvent.click(screen.getByRole("button", { name: /Last scanned/ }));
    await waitFor(() => expect(reposCalls().at(-1)?.query.get("sort")).toBe("last_scanned"));
    expect(
      screen.getByRole("columnheader", { name: /Last scanned/ }),
    ).toHaveAttribute("aria-sort", "descending");
  });

  it("has empty states that differ for admins and viewers", async () => {
    signIn("admin", {}, []);
    const first = renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByText("No repositories yet")).toBeInTheDocument();
    expect(screen.getByText(/Add a GitHub repository/)).toBeInTheDocument();
    first.unmount();

    signIn("viewer", {}, []);
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByText(/administrator needs to add/)).toBeInTheDocument();
  });

  it("shows an error state with a retry button when loading fails", async () => {
    signIn("viewer", {
      "GET /repos": () => errorResponse(500, "INTERNAL_ERROR", "Internal server error."),
    });
    renderWithProviders(<App />, { route: "/" });
    const alert = await screen.findByText("Internal server error.");
    expect(alert).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Try again" }).length).toBeGreaterThan(0);
  });

  it("triggers a scan from the table", async () => {
    const { calls } = signIn("admin", { "POST /repos/1/scan": () => jsonResponse({ id: 99 }, 202) });
    renderWithProviders(<App />, { route: "/" });
    await userEvent.click(await screen.findByRole("button", { name: "Scan o/r1 now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/repos/1/scan")).toBe(true));
  });

  it("shows the server's error when a scan cannot be started", async () => {
    signIn("admin", {
      "POST /repos/1/scan": () => errorResponse(409, "SCAN_IN_PROGRESS", "A scan is already queued or running for this repository."),
    });
    renderWithProviders(<App />, { route: "/" });
    await userEvent.click(await screen.findByRole("button", { name: "Scan o/r1 now" }));
    expect(await screen.findByText(/already queued or running/)).toBeInTheDocument();
  });

  it("disables Scan now while a scan is active", async () => {
    signIn("admin", {}, [repo(1, { active_scan_id: 3 })]);
    renderWithProviders(<App />, { route: "/" });
    expect(await screen.findByRole("button", { name: "Scan o/r1 now" })).toBeDisabled();
  });

  it("asks for confirmation before deleting, and cancel deletes nothing", async () => {
    const { calls } = signIn("admin", { "DELETE /repos/1": () => new Response(null, { status: 204 }) });
    renderWithProviders(<App />, { route: "/" });
    await userEvent.click(await screen.findByRole("button", { name: "Delete o/r1" }));
    const dialog = screen.getByRole("dialog", { name: "Delete repository?" });
    expect(within(dialog).getByText(/permanently deletes its scans/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Delete o/r1" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/repos/1")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("Escape closes the delete dialog", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/" });
    await userEvent.click(await screen.findByRole("button", { name: "Delete o/r1" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("add repository", () => {
  async function openModal() {
    renderWithProviders(<App />, { route: "/" });
    await userEvent.click(await screen.findByRole("button", { name: "Add repository" }));
    return screen.getByRole("dialog", { name: "Add repository" });
  }

  it("rejects a bad URL on the client without calling the server", async () => {
    const { calls } = signIn("admin");
    const dialog = await openModal();
    await userEvent.type(within(dialog).getByLabelText("GitHub repository URL"), "http://github.com/o/r");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add repository" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("https://github.com/owner/repo");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("asks for a URL when the field is empty", async () => {
    signIn("admin");
    const dialog = await openModal();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add repository" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Enter a repository URL.");
  });

  it("shows the server's error for an already tracked repository", async () => {
    signIn("admin", {
      "POST /repos": () => errorResponse(409, "REPO_ALREADY_EXISTS", "This repository is already being tracked."),
    });
    const dialog = await openModal();
    await userEvent.type(within(dialog).getByLabelText("GitHub repository URL"), "https://github.com/o/r1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add repository" }));
    expect(await within(dialog).findByText("This repository is already being tracked.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("explains a GitHub rate limit", async () => {
    signIn("admin", {
      "POST /repos": () => errorResponse(503, "GITHUB_RATE_LIMITED", "GitHub rate limit reached."),
    });
    const dialog = await openModal();
    await userEvent.type(within(dialog).getByLabelText("GitHub repository URL"), "https://github.com/o/r1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add repository" }));
    expect(await within(dialog).findByText(/rate limit was reached/)).toBeInTheDocument();
  });

  it("adds a valid repository and opens its page", async () => {
    const { calls } = signIn("admin", {
      "POST /repos": () => jsonResponse(repo(7, { full_name: "o/new" }), 201),
      "GET /repos/7": () => repo(7, { full_name: "o/new" }),
      "GET /repos/7/scans": () => ({ items: [], total: 0 }),
      "GET /scans/70": () => ({ id: 70, repo_id: 7, status: "completed", results: [] }),
    });
    const dialog = await openModal();
    await userEvent.type(within(dialog).getByLabelText("GitHub repository URL"), "  https://github.com/o/new  ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add repository" }));
    expect(await screen.findByRole("heading", { name: /o\/new/ })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/repos")?.body).toEqual({
      url: "https://github.com/o/new",
    });
  });

  it("opens straight from the /repos/new route", async () => {
    signIn("admin");
    renderWithProviders(<App />, { route: "/repos/new" });
    expect(await screen.findByRole("dialog", { name: "Add repository" })).toBeInTheDocument();
  });

  it("viewers never get the modal, even via the route", async () => {
    signIn("viewer");
    renderWithProviders(<App />, { route: "/repos/new" });
    await screen.findByText("o/r1");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("users page", () => {
  const users = [user("admin", { id: 1 }), user("viewer", { id: 2, email: "v@example.com" })];

  it("shows the server's LAST_ADMIN error inline", async () => {
    signIn("admin", {
      "GET /users": () => ({ items: users, total: 2 }),
      "PATCH /users/1": () => errorResponse(409, "LAST_ADMIN", "Cannot change the last active administrator."),
    });
    renderWithProviders(<App />, { route: "/users" });
    const select = await screen.findByLabelText("Role for admin@example.com");
    await userEvent.selectOptions(select, "viewer");
    expect(await screen.findByText("Cannot change the last active administrator.")).toBeInTheDocument();
  });

  it("changes a role and toggles active status", async () => {
    const { calls } = signIn("admin", {
      "GET /users": () => ({ items: users, total: 2 }),
      "PATCH /users/2": () => user("admin", { id: 2 }),
    });
    renderWithProviders(<App />, { route: "/users" });
    await userEvent.selectOptions(await screen.findByLabelText("Role for v@example.com"), "admin");
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "admin" }),
    );
    await userEvent.click(screen.getByRole("switch", { name: "Active: v@example.com" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ is_active: false }),
    );
  });

  it("creates a user and validates the form first", async () => {
    const { calls } = signIn("admin", {
      "GET /users": () => ({ items: users, total: 2 }),
      "POST /users": () => jsonResponse(user("viewer", { id: 3 }), 201),
    });
    renderWithProviders(<App />, { route: "/users" });
    await screen.findByText("v@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an email address.");
    await userEvent.type(screen.getByLabelText("Email"), "n@example.com");
    await userEvent.type(screen.getByLabelText("Temporary password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Password must be 8 to 128 characters.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await userEvent.clear(screen.getByLabelText("Temporary password"));
    await userEvent.type(screen.getByLabelText("Temporary password"), "test-long-enough-1");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("User created.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/users")?.body).toEqual({
      email: "n@example.com",
      password: "test-long-enough-1",
      role: "viewer",
    });
  });

  it("shows a duplicate-email error from the server", async () => {
    signIn("admin", {
      "GET /users": () => ({ items: users, total: 2 }),
      "POST /users": () => errorResponse(409, "EMAIL_TAKEN", "A user with this email already exists."),
    });
    renderWithProviders(<App />, { route: "/users" });
    await userEvent.type(await screen.findByLabelText("Email"), "v@example.com");
    await userEvent.type(screen.getByLabelText("Temporary password"), "test-long-enough-1");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("A user with this email already exists.")).toBeInTheDocument();
  });
});

describe("account page", () => {
  async function fill(current: string, next: string, confirm: string) {
    await userEvent.type(await screen.findByLabelText("Current password"), current);
    await userEvent.type(screen.getByLabelText("New password"), next);
    await userEvent.type(screen.getByLabelText("Confirm new password"), confirm);
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
  }

  it("checks the new password locally", async () => {
    const { calls } = signIn("viewer");
    renderWithProviders(<App />, { route: "/account" });
    await fill("old-password-1", "short", "short");
    expect(screen.getByRole("alert")).toHaveTextContent("8 to 128 characters");
    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.clear(screen.getByLabelText("Confirm new password"));
    await userEvent.type(screen.getByLabelText("New password"), "new-password-1");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "different-1");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("reports a wrong current password without logging the user out", async () => {
    signIn("viewer", {
      "POST /auth/change-password": () => errorResponse(400, "BAD_REQUEST", "Current password is incorrect."),
    });
    renderWithProviders(<App />, { route: "/account" });
    await fill("test-wrong-password", "new-password-1", "new-password-1");
    expect(await screen.findByText("Your current password is incorrect.")).toBeInTheDocument();
    expect(localStorage.getItem("gitwell.token")).toBe("tok");
  });

  it("signs out and returns to login after a successful change", async () => {
    signIn("viewer", { "POST /auth/change-password": () => new Response(null, { status: 204 }) });
    renderWithProviders(<App />, { route: "/account" });
    await fill("old-password-1", "new-password-1", "new-password-1");
    expect(await screen.findByText("Password changed. Please sign in again.")).toBeInTheDocument();
    expect(localStorage.getItem("gitwell.token")).toBeNull();
  });
});
