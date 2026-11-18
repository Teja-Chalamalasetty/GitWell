import { useId, useState } from "react";
import type { RuleResult } from "../api/types";
import { parseFindings, parseManifests, stringList } from "../lib/ruleDetails";
import { StatusBadge } from "./Badges";
import { Markdown } from "./Markdown";

// `details` comes from the server as arbitrary JSON, so every field is checked before use.
// Repo-derived strings (paths, package names) are only ever rendered as text.

function SecretsDetails({ details, message }: { details: Record<string, unknown>; message: string }) {
  const findings = parseFindings(details);
  return (
    <div className="space-y-2">
      {findings.length > 0 && (
        <table className="w-full text-sm">
          <caption className="sr-only">Potential secrets: file, line, pattern and confidence</caption>
          <tbody>
            {findings.map((f, i) => (
              <tr key={`${f.path}:${f.line}:${f.pattern}:${i}`} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="py-1 pr-3 font-mono text-xs">
                  {f.line === null ? f.path : `${f.path}:${f.line}`}
                </td>
                <td className="py-1 text-zinc-600 dark:text-zinc-400">
                  — {f.pattern} ({f.confidence})
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {details.truncated === true && (
        <p className="text-xs text-zinc-500">Only the first 100 findings are shown.</p>
      )}
      <p className="text-xs text-zinc-500">
        Only the file and line are shown, never the secret itself.
        {/* The pass message already says this; do not repeat it. */}
        {!message.includes("Git history is not scanned") && " Git history is not scanned."}
      </p>
    </div>
  );
}

function DependencyDetails({ details }: { details: Record<string, unknown> }) {
  const manifests = parseManifests(details);
  if (manifests.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500">
            <th className="py-1 pr-3 font-medium">Manifest</th>
            <th className="py-1 pr-3 font-medium">Lock file</th>
            <th className="py-1 pr-3 font-medium">Verdict</th>
            <th className="py-1 font-medium">Unpinned</th>
          </tr>
        </thead>
        <tbody>
          {manifests.map((m) => (
            <tr key={m.path} className="border-t border-zinc-100 align-top dark:border-zinc-800">
              <td className="py-1 pr-3 font-mono text-xs">{m.path}</td>
              <td className="py-1 pr-3 font-mono text-xs">{m.lock_file ?? "none"}</td>
              <td className="py-1 pr-3">
                {m.verdict}
                {m.parse_error && " (could not be parsed)"}
              </td>
              <td className="py-1 text-xs">
                {m.unpinned.length > 0 ? m.unpinned.join(", ") : m.total === null ? "—" : "none"}
                {m.total !== null && (
                  <span className="text-zinc-500">
                    {" "}
                    ({m.total} total)
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ListDetails({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="text-sm">
      <span className="text-zinc-500">{label}: </span>
      <span className="font-mono text-xs">{items.join(", ")}</span>
    </div>
  );
}

function Details({ result }: { result: RuleResult }) {
  const d = result.details;
  switch (result.rule_id) {
    case "secrets":
      return <SecretsDetails details={d} message={result.message} />;
    case "dependencies":
      return <DependencyDetails details={d} />;
    case "tests":
      return <ListDetails label="Sample test files" items={stringList(d.sample)} />;
    case "ci":
      return <ListDetails label="CI files" items={stringList(d.files)} />;
    default:
      return null;
  }
}

export function RuleCard({ result }: { result: RuleResult }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <article className="card p-4" data-rule={result.rule_id}>
      <header className="flex flex-wrap items-center gap-2">
        <StatusBadge status={result.status} />
        <h3 className="font-semibold">{result.rule_name}</h3>
        <span className="ml-auto text-xs text-zinc-500">weight {result.weight}</span>
      </header>
      <p className="mt-2 text-sm">{result.message}</p>
      <div className="mt-2">
        <Details result={result} />
      </div>
      {result.fix_tip && (
        <div className="mt-3">
          <button
            type="button"
            className="text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide fix" : "How to fix"}
          </button>
          {open && (
            <div id={panelId} className="mt-2 rounded-md bg-zinc-50 p-3 dark:bg-zinc-800/50">
              <Markdown>{result.fix_tip}</Markdown>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export function RuleCards({ results }: { results: RuleResult[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {results.map((r) => (
        <RuleCard key={r.rule_id} result={r} />
      ))}
    </div>
  );
}
