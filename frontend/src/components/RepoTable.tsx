import { useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../api/client";
import { useDeleteRepo, useTriggerScan } from "../api/hooks";
import type { RepoSort, RepoSummary, SortOrder } from "../api/types";
import { formatDateTime, relativeTime } from "../lib/format";
import { ArchivedBadge, Chip, DeltaBadge, ScoreBadge } from "./Badges";
import { ConfirmDialog } from "./Modal";

interface Props {
  repos: RepoSummary[];
  isAdmin: boolean;
  sort: RepoSort;
  order: SortOrder;
  onSort: (key: RepoSort) => void;
}

function SortHeader({
  label,
  column,
  sort,
  order,
  onSort,
}: {
  label: string;
  column: RepoSort;
  sort: RepoSort;
  order: SortOrder;
  onSort: (key: RepoSort) => void;
}) {
  const active = sort === column;
  return (
    <th
      className="th"
      scope="col"
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        className="inline-flex items-center gap-1 uppercase tracking-wide hover:text-zinc-900 dark:hover:text-zinc-100"
        onClick={() => onSort(column)}
      >
        {label}
        <span aria-hidden className={active ? "" : "opacity-30"}>
          {active ? (order === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

function StatusChip({ repo }: { repo: RepoSummary }) {
  if (repo.active_scan_id !== null) return <Chip tone="info">Scanning…</Chip>;
  if (repo.latest_scan?.status === "failed") {
    return (
      <Chip tone="fail" title="The most recent scan did not complete">
        Last scan failed: {repo.latest_scan.error_code ?? "UNKNOWN"}
      </Chip>
    );
  }
  if (!repo.latest_scan) return <span className="text-zinc-400">Not scanned</span>;
  return <span className="text-zinc-500">Idle</span>;
}

export function RepoTable({ repos, isAdmin, sort, order, onSort }: Props) {
  const trigger = useTriggerScan();
  const remove = useDeleteRepo();
  const [toDelete, setToDelete] = useState<RepoSummary | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  function scanNow(repo: RepoSummary) {
    setActionError(null);
    trigger.mutate(repo.id, { onError: (e) => setActionError(`${repo.full_name}: ${errorMessage(e)}`) });
  }

  return (
    <>
      {actionError && (
        <p role="alert" className="mb-2 text-sm text-red-600 dark:text-red-400">
          {actionError}
        </p>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-zinc-200 dark:border-zinc-800">
            <tr>
              <SortHeader label="Repository" column="name" sort={sort} order={order} onSort={onSort} />
              <SortHeader label="Score" column="score" sort={sort} order={order} onSort={onSort} />
              <th className="th" scope="col">
                Δ
              </th>
              <SortHeader
                label="Last scanned"
                column="last_scanned"
                sort={sort}
                order={order}
                onSort={onSort}
              />
              <th className="th" scope="col">
                Status
              </th>
              {isAdmin && (
                <th className="th" scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {repos.map((repo) => {
              const done = repo.latest_completed_scan;
              return (
                <tr key={repo.id}>
                  <td className="td">
                    <Link
                      to={`/repos/${repo.id}`}
                      className="font-medium text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {repo.full_name}
                    </Link>
                    {repo.is_archived && (
                      <span className="ml-2">
                        <ArchivedBadge />
                      </span>
                    )}
                    {repo.is_private && (
                      <span className="ml-2">
                        <Chip>Private</Chip>
                      </span>
                    )}
                  </td>
                  <td className="td">
                    {done ? (
                      <ScoreBadge
                        score={done.score}
                        grade={done.grade}
                        rulesErrored={done.rules_errored}
                        isPartial={done.is_partial}
                      />
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </td>
                  <td className="td">
                    <DeltaBadge delta={repo.score_delta} />
                  </td>
                  <td className="td text-zinc-500" title={formatDateTime(repo.latest_scan?.created_at)}>
                    {relativeTime(repo.latest_scan?.created_at)}
                  </td>
                  <td className="td">
                    <StatusChip repo={repo} />
                  </td>
                  {isAdmin && (
                    <td className="td whitespace-nowrap text-right">
                      <button
                        type="button"
                        className="btn-secondary btn-sm mr-2"
                        disabled={repo.active_scan_id !== null || trigger.isPending}
                        onClick={() => scanNow(repo)}
                        aria-label={`Scan ${repo.full_name} now`}
                      >
                        Scan now
                      </button>
                      <button
                        type="button"
                        className="btn-secondary btn-sm text-red-600 dark:text-red-400"
                        onClick={() => {
                          remove.reset();
                          setToDelete(repo);
                        }}
                        aria-label={`Delete ${repo.full_name}`}
                      >
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {toDelete && (
        <ConfirmDialog
          title="Delete repository?"
          confirmLabel="Delete"
          busy={remove.isPending}
          error={remove.error ? errorMessage(remove.error) : null}
          onCancel={() => setToDelete(null)}
          onConfirm={() => remove.mutate(toDelete.id, { onSuccess: () => setToDelete(null) })}
        >
          <p>
            This stops tracking <strong>{toDelete.full_name}</strong> and permanently deletes its
            scans, results and history. The repository on GitHub is not touched.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
