import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { errorMessage } from "../api/client";
import { useRepo, useRepoScans, useScan, useTriggerScan } from "../api/hooks";
import { ArchivedBadge, Chip, GradeBadge, ScoreBadge } from "../components/Badges";
import { ScoreHistoryChart } from "../components/Charts";
import { RepoIssues } from "../components/RepoIssues";
import { ScanResults } from "../components/ScanResults";
import { Banner, EmptyState, ErrorState, Spinner } from "../components/States";
import { useAuth } from "../hooks/useAuth";
import { useScanPolling } from "../hooks/useScanPolling";
import { formatDateTime, formatScore, relativeTime, shortSha } from "../lib/format";
import { SCAN_STATUS_LABEL } from "../lib/status";
import { NotFoundPage } from "./NotFound";

export function RepoDetailPage() {
  const params = useParams();
  const repoId = Number(params.id);
  if (!Number.isInteger(repoId) || repoId <= 0) return <NotFoundPage />;
  return <RepoDetail repoId={repoId} />;
}

function RepoDetail({ repoId }: { repoId: number }) {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const repo = useRepo(repoId);
  const completed = useRepoScans(repoId, "completed");
  const allScans = useRepoScans(repoId, "all", 50);
  const trigger = useTriggerScan();
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const data = repo.data;
  const latestDoneId = data?.latest_completed_scan?.id ?? null;
  const lastFailedId = data?.latest_scan?.status === "failed" ? data.latest_scan.id : null;
  const activeScan = useScanPolling(data?.active_scan_id, repoId);
  const latestDone = useScan(latestDoneId);
  const lastFailed = useScan(lastFailedId);

  if (repo.isPending) return <Spinner label="Loading repository…" />;
  if (repo.isError || !data) {
    return <ErrorState error={repo.error ?? new Error("Not found")} onRetry={() => void repo.refetch()} />;
  }

  const done = data.latest_completed_scan;
  const active = data.active_scan_id !== null;
  const history = [...(completed.data?.items ?? [])]
    .filter((s) => s.finished_at !== null)
    .sort((a, b) => Date.parse(a.finished_at ?? "") - Date.parse(b.finished_at ?? ""))
    .map((s) => ({ id: s.id, finished_at: s.finished_at ?? "", score: s.score }));

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-sm text-zinc-500">
        <Link to="/" className="hover:underline">
          Dashboard
        </Link>{" "}
        / <span>{data.full_name}</span>
      </nav>

      <header className="flex flex-wrap items-start gap-4">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-bold">
            {data.full_name}
            {data.is_archived && <ArchivedBadge />}
            {data.is_private && <Chip>Private</Chip>}
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            <a
              href={data.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline dark:text-blue-400"
            >
              View on GitHub ↗
            </a>
            <span className="mx-2">·</span>
            branch <span className="font-mono">{data.default_branch}</span>
            {done && (
              <>
                <span className="mx-2">·</span>
                commit <span className="font-mono">{shortSha(done.commit_sha)}</span>
              </>
            )}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-4">
          {done ? (
            <ScoreBadge
              score={done.score}
              grade={done.grade}
              rulesErrored={done.rules_errored}
              isPartial={done.is_partial}
            />
          ) : (
            <span className="text-sm text-zinc-500">Not scanned yet</span>
          )}
          {isAdmin && (
            <button
              type="button"
              className="btn-primary"
              disabled={active || trigger.isPending}
              onClick={() => {
                setTriggerError(null);
                trigger.mutate(repoId, { onError: (e) => setTriggerError(errorMessage(e)) });
              }}
            >
              {active ? "Scanning…" : "Scan now"}
            </button>
          )}
        </div>
      </header>

      {triggerError && <Banner tone="fail">{triggerError}</Banner>}
      {active && (
        <Banner tone="info">
          A scan is {activeScan.data?.status === "running" ? "running" : "queued"}… this page
          updates by itself.
        </Banner>
      )}
      {!active && lastFailed.data && (
        <Banner tone="fail">
          <strong>The most recent scan failed.</strong>{" "}
          {lastFailed.data.error_message ?? "It did not complete."}
          {lastFailed.data.error_code && (
            <span className="ml-2 font-mono text-xs">({lastFailed.data.error_code})</span>
          )}
          {done && " The results below are from the last successful scan."}
        </Banner>
      )}

      <section aria-labelledby="history-heading" className="card p-4">
        <h2 id="history-heading" className="mb-3 text-sm font-semibold">
          Score history
        </h2>
        {completed.isPending ? (
          <Spinner />
        ) : completed.isError ? (
          <ErrorState error={completed.error} onRetry={() => void completed.refetch()} />
        ) : history.length === 0 ? (
          <EmptyState title="No completed scans yet" />
        ) : (
          <ScoreHistoryChart data={history} />
        )}
      </section>

      <section aria-labelledby="results-heading" className="space-y-3">
        <h2 id="results-heading" className="text-lg font-semibold">
          Latest results
        </h2>
        {latestDoneId === null ? (
          <EmptyState title="No results yet">Results appear after the first scan completes.</EmptyState>
        ) : latestDone.isPending ? (
          <Spinner label="Loading results…" />
        ) : latestDone.isError ? (
          <ErrorState error={latestDone.error} onRetry={() => void latestDone.refetch()} />
        ) : (
          <ScanResults scan={latestDone.data} />
        )}
      </section>

      <RepoIssues repo={data} isAdmin={isAdmin} />

      <section aria-labelledby="scans-heading" className="space-y-3">
        <h2 id="scans-heading" className="text-lg font-semibold">
          Past scans
          {allScans.data && (
            <span className="ml-2 text-sm font-normal text-zinc-500">
              ({allScans.data.items.length} of {allScans.data.total})
            </span>
          )}
        </h2>
        {allScans.isPending ? (
          <Spinner />
        ) : allScans.isError ? (
          <ErrorState error={allScans.error} onRetry={() => void allScans.refetch()} />
        ) : allScans.data.items.length === 0 ? (
          <EmptyState title="No scans yet" />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[560px]">
              <thead className="border-b border-zinc-200 dark:border-zinc-800">
                <tr>
                  <th className="th" scope="col">When</th>
                  <th className="th" scope="col">Status</th>
                  <th className="th" scope="col">Score</th>
                  <th className="th" scope="col">Trigger</th>
                  <th className="th" scope="col">Commit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {allScans.data.items.map((scan) => (
                  <tr
                    key={scan.id}
                    className="cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                    onClick={() => navigate(`/scans/${scan.id}`)}
                  >
                    <td className="td" title={formatDateTime(scan.created_at)}>
                      <Link
                        to={`/scans/${scan.id}`}
                        className="text-blue-600 hover:underline dark:text-blue-400"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {relativeTime(scan.created_at)}
                      </Link>
                    </td>
                    <td className="td">
                      {scan.status === "failed" ? (
                        <Chip tone="fail" title={scan.error_message ?? undefined}>
                          Failed: {scan.error_code}
                        </Chip>
                      ) : (
                        SCAN_STATUS_LABEL[scan.status]
                      )}
                    </td>
                    <td className="td">
                      <span className="inline-flex items-center gap-2">
                        <span className="tabular-nums">{formatScore(scan.score)}</span>
                        <GradeBadge grade={scan.grade} />
                      </span>
                    </td>
                    <td className="td text-zinc-500">{scan.trigger.replace("_", " ")}</td>
                    <td className="td font-mono text-xs">{shortSha(scan.commit_sha)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
