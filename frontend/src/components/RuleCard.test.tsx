import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { RuleResult } from "../api/types";
import { parseFindings, parseManifests } from "../lib/ruleDetails";
import { RuleCard } from "./RuleCard";

function result(overrides: Partial<RuleResult>): RuleResult {
  return {
    rule_id: "readme",
    rule_name: "README",
    status: "fail",
    weight: 15,
    display_order: 20,
    message: "No README found in the repository root.",
    details: {},
    fix_tip: "Add a `README.md` with **setup** steps.",
    ...overrides,
  };
}

describe("RuleCard", () => {
  it("shows status, name, weight and message", () => {
    render(<RuleCard result={result({})} />);
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "README" })).toBeInTheDocument();
    expect(screen.getByText("weight 15")).toBeInTheDocument();
    expect(screen.getByText("No README found in the repository root.")).toBeInTheDocument();
  });

  it("expands the fix tip as markdown", async () => {
    render(<RuleCard result={result({})} />);
    const toggle = screen.getByRole("button", { name: "How to fix" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("setup")).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Hide fix" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("setup").tagName).toBe("STRONG");
    expect(screen.getByText("README.md").tagName).toBe("CODE");

    await userEvent.click(screen.getByRole("button", { name: "Hide fix" }));
    expect(screen.queryByText("setup")).not.toBeInTheDocument();
  });

  it("has no fix button for a rule that no longer exists (empty tip)", () => {
    render(<RuleCard result={result({ fix_tip: "" })} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders the secrets table as path:line — pattern (confidence), and the history caveat", () => {
    render(
      <RuleCard
        result={result({
          rule_id: "secrets",
          rule_name: "No committed secrets",
          message: "2 potential secret(s) found in 2 file(s).",
          details: {
            findings: [
              { path: "app/config.py", line: 12, pattern: "aws_access_key_id", confidence: "high" },
              { path: ".env", line: null, pattern: "committed_env_file", confidence: "medium" },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("app/config.py:12")).toBeInTheDocument();
    expect(screen.getByText("— aws_access_key_id (high)")).toBeInTheDocument();
    expect(screen.getByText(".env")).toBeInTheDocument(); // file-level finding: no line number
    expect(screen.getByText("— committed_env_file (medium)")).toBeInTheDocument();
    expect(screen.getByText(/Git history is not scanned/)).toBeInTheDocument();
    expect(screen.getByText(/never the secret itself/)).toBeInTheDocument();
  });

  it("does not repeat the history caveat when the message already says it", () => {
    render(
      <RuleCard
        result={result({
          rule_id: "secrets",
          status: "pass",
          message: "No secrets found in the current tree of main@abc1234. Git history is not scanned.",
          details: { findings: [] },
        })}
      />,
    );
    expect(screen.getAllByText(/Git history is not scanned/)).toHaveLength(1);
  });

  it("notes when the secret findings were truncated", () => {
    render(
      <RuleCard
        result={result({ rule_id: "secrets", details: { findings: [], truncated: true } })}
      />,
    );
    expect(screen.getByText("Only the first 100 findings are shown.")).toBeInTheDocument();
  });

  it("renders repo-derived strings as text, never as HTML", () => {
    const { container } = render(
      <RuleCard
        result={result({
          rule_id: "secrets",
          details: {
            findings: [
              { path: "<img src=x onerror=alert(1)>.py", line: 1, pattern: "p", confidence: "low" },
            ],
          },
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>.py:1")).toBeInTheDocument();
  });

  it("renders the dependency manifests table", () => {
    render(
      <RuleCard
        result={result({
          rule_id: "dependencies",
          rule_name: "Pinned dependencies",
          status: "warn",
          details: {
            manifests: [
              {
                path: "backend/pyproject.toml",
                ecosystem: "python",
                lock_file: "backend/uv.lock",
                verdict: "pass",
                total: 14,
                unpinned: [],
              },
              {
                path: "web/package.json",
                ecosystem: "node",
                lock_file: null,
                verdict: "warn",
                total: 3,
                unpinned: ["left-pad", "react"],
              },
              { path: "broken/package.json", ecosystem: "node", verdict: "warn", parse_error: true },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("backend/pyproject.toml")).toBeInTheDocument();
    expect(screen.getByText("backend/uv.lock")).toBeInTheDocument();
    expect(screen.getByText("left-pad, react")).toBeInTheDocument();
    const lockedRow = screen.getByText("backend/pyproject.toml").closest("tr") as HTMLElement;
    expect(within(lockedRow).getByText(/none/)).toBeInTheDocument(); // nothing unpinned
    expect(within(lockedRow).getByText(/14 total/)).toBeInTheDocument();
    const unlockedRow = screen.getByText("web/package.json").closest("tr") as HTMLElement;
    expect(within(unlockedRow).getByText("none")).toBeInTheDocument(); // no lock file
    expect(screen.getByText(/could not be parsed/)).toBeInTheDocument();
  });

  it("renders sample test files", () => {
    render(
      <RuleCard
        result={result({
          rule_id: "tests",
          status: "pass",
          details: { test_file_count: 2, sample: ["tests/test_a.py", "tests/test_b.py"] },
        })}
      />,
    );
    expect(screen.getByText("tests/test_a.py, tests/test_b.py")).toBeInTheDocument();
  });

  it("tolerates malformed details without crashing", () => {
    render(
      <RuleCard
        result={result({
          rule_id: "dependencies",
          details: { manifests: "nope" },
        })}
      />,
    );
    expect(screen.getByRole("heading", { name: "README" })).toBeInTheDocument();
  });
});

describe("detail parsers", () => {
  it("parseFindings ignores junk entries", () => {
    expect(parseFindings({ findings: [1, null, "x", { path: "a", line: 3, pattern: "p", confidence: "c" }] }))
      .toEqual([{ path: "a", line: 3, pattern: "p", confidence: "c" }]);
    expect(parseFindings({})).toEqual([]);
  });

  it("parseManifests normalises missing fields", () => {
    expect(parseManifests({ manifests: [{ path: "p" }] })).toEqual([
      { path: "p", ecosystem: "", lock_file: null, verdict: "", total: null, unpinned: [], parse_error: false },
    ]);
  });
});
