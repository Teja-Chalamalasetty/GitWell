// Client-side twin of the backend's strict URL check (PRD 12.1). The server still decides.

export const REPO_URL_RE =
  /^https:\/\/github\.com\/(?<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)\/(?<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/;

const BAD_NAMES = new Set([".", "..", ".git"]);

export const REPO_URL_HELP = "Enter a URL like https://github.com/owner/repo";

export interface ParsedRepoUrl {
  owner: string;
  name: string;
}

export function parseRepoUrl(input: string): ParsedRepoUrl | null {
  const match = REPO_URL_RE.exec(input.trim());
  if (!match?.groups) return null;
  const { owner, name } = match.groups as { owner: string; name: string };
  if (owner.includes("--") || BAD_NAMES.has(name)) return null;
  return { owner, name };
}

/** An error message, or null if the URL is acceptable. */
export function validateRepoUrl(input: string): string | null {
  if (input.trim() === "") return "Enter a repository URL.";
  return parseRepoUrl(input) ? null : REPO_URL_HELP;
}
