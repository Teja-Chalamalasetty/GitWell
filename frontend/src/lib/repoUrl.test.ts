import { describe, expect, it } from "vitest";
import { parseRepoUrl, validateRepoUrl } from "./repoUrl";

const VALID: [string, { owner: string; name: string }][] = [
  ["https://github.com/psf/requests", { owner: "psf", name: "requests" }],
  ["https://github.com/psf/requests/", { owner: "psf", name: "requests" }],
  ["https://github.com/psf/requests.git", { owner: "psf", name: "requests" }],
  ["https://github.com/psf/requests.git/", { owner: "psf", name: "requests" }],
  ["  https://github.com/psf/requests  ", { owner: "psf", name: "requests" }],
  ["https://github.com/a/b", { owner: "a", name: "b" }],
  ["https://github.com/Some-Org/My_Repo.v2", { owner: "Some-Org", name: "My_Repo.v2" }],
  ["https://github.com/o/a.b.c", { owner: "o", name: "a.b.c" }],
  ["https://github.com/o/.github", { owner: "o", name: ".github" }],
  ["https://github.com/o/-dash", { owner: "o", name: "-dash" }],
  ["https://github.com/o/repo.git.git", { owner: "o", name: "repo.git" }],
  [`https://github.com/${"a".repeat(39)}/r`, { owner: "a".repeat(39), name: "r" }],
  [`https://github.com/o/${"n".repeat(100)}`, { owner: "o", name: "n".repeat(100) }],
  ["https://github.com/a-b/r", { owner: "a-b", name: "r" }],
];

const INVALID = [
  "",
  "   ",
  "github.com/psf/requests",
  "http://github.com/psf/requests",
  "HTTPS://github.com/psf/requests",
  "https://GitHub.com/psf/requests",
  "https://www.github.com/psf/requests",
  "https://gist.github.com/psf/requests",
  "https://gitlab.com/psf/requests",
  "https://github.com.evil.com/psf/requests",
  "https://evil.com/github.com/psf/requests",
  "https://github.com/psf",
  "https://github.com/",
  "https://github.com",
  "https://github.com/psf/requests/tree/main",
  "https://github.com/psf/requests/issues",
  "https://github.com/psf/requests?tab=readme",
  "https://github.com/psf/requests#readme",
  "https://user@github.com/psf/requests",
  "https://user:pass@github.com/psf/requests",
  "https://github.com:443/psf/requests",
  "https://github.com/-bad/requests",
  "https://github.com/bad-/requests",
  "https://github.com/ba--d/requests",
  `https://github.com/${"a".repeat(40)}/r`,
  `https://github.com/o/${"n".repeat(101)}`,
  "https://github.com/o/.",
  "https://github.com/o/..",
  "https://github.com/o/.git",
  "https://github.com/o/re po",
  "https://github.com/o/re%20po",
  "https://github.com/o/r\nhttps://evil.com",
  "https://github.com//r",
  "https://github.com/o//r",
  "https://github.com/o_o/r",
  "ftp://github.com/o/r",
  "git@github.com:o/r.git",
  "https://github.com/o/r.git/extra",
];

describe("parseRepoUrl", () => {
  it.each(VALID)("accepts %s", (url, expected) => {
    expect(parseRepoUrl(url)).toEqual(expected);
  });

  it.each(INVALID)("rejects %j", (url) => {
    expect(parseRepoUrl(url)).toBeNull();
  });

  it("a trailing newline is stripped, like the server's strip()", () => {
    expect(parseRepoUrl("https://github.com/o/r\n")).toEqual({ owner: "o", name: "r" });
  });

  it("has at least 20 table cases", () => {
    expect(VALID.length + INVALID.length).toBeGreaterThanOrEqual(20);
  });
});

describe("validateRepoUrl", () => {
  it("returns null for a valid URL", () => {
    expect(validateRepoUrl("https://github.com/o/r")).toBeNull();
  });
  it("asks for input when empty", () => {
    expect(validateRepoUrl("  ")).toBe("Enter a repository URL.");
  });
  it("explains the expected format otherwise", () => {
    expect(validateRepoUrl("http://github.com/o/r")).toContain("https://github.com/owner/repo");
  });
});
