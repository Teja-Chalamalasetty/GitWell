// `details` comes from the server as arbitrary JSON, so every field is checked before use.

export interface Finding {
  path: string;
  line: number | null;
  pattern: string;
  confidence: string;
}

export interface Manifest {
  path: string;
  ecosystem: string;
  lock_file: string | null;
  verdict: string;
  total: number | null;
  unpinned: string[];
  parse_error: boolean;
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export function parseFindings(details: Record<string, unknown>): Finding[] {
  const raw = details.findings;
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRecord).map((f) => ({
    path: String(f.path ?? ""),
    line: typeof f.line === "number" ? f.line : null,
    pattern: String(f.pattern ?? ""),
    confidence: String(f.confidence ?? ""),
  }));
}

export function parseManifests(details: Record<string, unknown>): Manifest[] {
  const raw = details.manifests;
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRecord).map((m) => ({
    path: String(m.path ?? ""),
    ecosystem: String(m.ecosystem ?? ""),
    lock_file: typeof m.lock_file === "string" ? m.lock_file : null,
    verdict: String(m.verdict ?? ""),
    total: typeof m.total === "number" ? m.total : null,
    unpinned: Array.isArray(m.unpinned) ? m.unpinned.map(String) : [],
    parse_error: m.parse_error === true,
  }));
}

export function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}
