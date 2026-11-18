import type { ScanDetail } from "../api/types";
import { RuleCards } from "./RuleCard";
import { Banner } from "./States";

/** Banners + rule cards for one scan (shared by the repo page and the scan page). */
export function ScanResults({ scan }: { scan: ScanDetail }) {
  return (
    <div className="space-y-3">
      {scan.status === "failed" && (
        <Banner tone="fail">
          <strong>Scan failed.</strong> {scan.error_message ?? "The scan did not complete."}
          {scan.error_code && <span className="ml-2 font-mono text-xs">({scan.error_code})</span>}
        </Banner>
      )}
      {scan.status === "completed" && scan.is_partial && (
        <Banner tone="info">
          <strong>Partial scan.</strong> Not every file could be read (the repository is large), so
          results may be incomplete.
        </Banner>
      )}
      {scan.status === "completed" && scan.rules_errored > 0 && (
        <Banner tone="warn">
          <strong>Incomplete score</strong> ({scan.rules_errored} rule
          {scan.rules_errored === 1 ? "" : "s"} errored).
          {scan.score === null && " A score could not be calculated."}
        </Banner>
      )}
      {scan.results.length > 0 && <RuleCards results={scan.results} />}
    </div>
  );
}
