import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useRepo } from "../api/hooks";
import { ScoreBadge } from "../components/Badges";
import { ScanResults } from "../components/ScanResults";
import { Banner, ErrorState, Spinner } from "../components/States";
import { useScanPolling } from "../hooks/useScanPolling";
import { formatDateTime, shortSha } from "../lib/format";
import { SCAN_STATUS_LABEL, isActive } from "../lib/status";
import { NotFoundPage } from "./NotFound";

export function ScanDetailPage() {
  const params = useParams();
  const scanId = Number(params.id);
  if (!Number.isInteger(scanId) || scanId <= 0) return <NotFoundPage />;
  return <ScanDetail scanId={scanId} />;
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function ScanDetail({ scanId }: { scanId: number }) {
  const scan = useScanPolling(scanId, undefined);
  const repo = useRepo(scan.data?.repo_id ?? 0);

  if (scan.isPending) return <Spinner label="Loading scan…" />;
  if (scan.isError || !scan.data) {
    return <ErrorState error={scan.error ?? new Error("Not found")} onRetry={() => void scan.refetch()} />;
  }
  const data = scan.data;

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-sm text-zinc-500">
        <Link to="/" className="hover:underline">
          Dashboard
        </Link>{" "}
        /{" "}
        <Link to={`/repos/${data.repo_id}`} className="hover:underline">
          {repo.data?.full_name ?? "Repository"}
        </Link>{" "}
        / <span>Scan #{data.id}</span>
      </nav>

      <header className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-bold">Scan #{data.id}</h1>
        {data.status === "completed" && (
          <ScoreBadge
            score={data.score}
            grade={data.grade}
            rulesErrored={data.rules_errored}
            isPartial={data.is_partial}
          />
        )}
      </header>

      {isActive(data.status) && (
        <Banner tone="info">
          This scan is {data.status}… the page updates by itself.
        </Banner>
      )}

      <dl className="card grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Meta label="Status">{SCAN_STATUS_LABEL[data.status]}</Meta>
        <Meta label="Trigger">{data.trigger.replace("_", " ")}</Meta>
        <Meta label="Queued">{formatDateTime(data.created_at)}</Meta>
        <Meta label="Finished">{formatDateTime(data.finished_at)}</Meta>
        <Meta label="Commit">
          <span className="font-mono">{shortSha(data.commit_sha)}</span>
        </Meta>
        <Meta label="Snapshot">{data.snapshot_mode ?? "—"}</Meta>
        <Meta label="Rule set">
          <span className="font-mono">{data.ruleset_version ?? "—"}</span>
        </Meta>
      </dl>

      <ScanResults scan={data} />
    </div>
  );
}
