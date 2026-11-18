import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { keys, useDashboard, useRepos } from "../api/hooks";
import type { RepoSort, SortOrder } from "../api/types";
import { AddRepoModal } from "../components/AddRepoModal";
import { FailuresChart, GradeBar, TrendChart } from "../components/Charts";
import { RepoTable } from "../components/RepoTable";
import { EmptyState, ErrorState, Spinner } from "../components/States";
import { useAuth } from "../hooks/useAuth";

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="text-sm text-zinc-500">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card p-4">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

// Default direction when a column is first chosen.
const DEFAULT_ORDER: Record<RepoSort, SortOrder> = { name: "asc", score: "desc", last_scanned: "desc" };

export function DashboardPage({ addOpen = false }: { addOpen?: boolean }) {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [sort, setSort] = useState<RepoSort>("name");
  const [order, setOrder] = useState<SortOrder>("asc");
  const [adding, setAdding] = useState(addOpen);

  const summary = useDashboard();
  const repos = useRepos(sort, order);

  // When the last running scan finishes, refresh the charts and stat cards.
  const anyActive = repos.data?.items.some((r) => r.active_scan_id !== null) ?? false;
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !anyActive) void qc.invalidateQueries({ queryKey: keys.dashboard });
    wasActive.current = anyActive;
  }, [anyActive, qc]);

  function onSort(column: RepoSort) {
    if (column === sort) {
      setOrder(order === "asc" ? "desc" : "asc");
    } else {
      setSort(column);
      setOrder(DEFAULT_ORDER[column]);
    }
  }

  function closeAdd() {
    setAdding(false);
    if (addOpen) navigate("/", { replace: true });
  }

  const s = summary.data;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        {isAdmin && (
          <button type="button" className="btn-primary ml-auto" onClick={() => setAdding(true)}>
            Add repository
          </button>
        )}
      </div>

      {summary.isPending ? (
        <Spinner label="Loading summary…" />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        s && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard
                label="Repositories tracked"
                value={String(s.repo_count)}
                hint={`${s.scanned_repo_count} scanned`}
              />
              <StatCard
                label="Average score"
                value={s.average_score === null ? "—" : s.average_score.toFixed(1)}
              />
              <StatCard label="Repositories graded F" value={String(s.grade_distribution.F ?? 0)} />
            </div>

            <Panel title="Grade distribution">
              <GradeBar distribution={s.grade_distribution} />
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="Most common failures">
                {s.scanned_repo_count === 0 ? (
                  <EmptyState title="No scan results yet" />
                ) : (
                  <FailuresChart data={s.rule_failures} />
                )}
              </Panel>
              <Panel title="Average score, last 30 days">
                {s.score_trend.length === 0 ? (
                  <EmptyState title="No scores in the last 30 days" />
                ) : (
                  <TrendChart data={s.score_trend} />
                )}
              </Panel>
            </div>
          </>
        )
      )}

      <section aria-labelledby="repos-heading" className="space-y-3">
        <h2 id="repos-heading" className="text-lg font-semibold">
          Repositories
        </h2>
        {repos.isPending ? (
          <Spinner label="Loading repositories…" />
        ) : repos.isError ? (
          <ErrorState error={repos.error} onRetry={() => void repos.refetch()} />
        ) : repos.data.items.length === 0 ? (
          <EmptyState title="No repositories yet">
            {isAdmin
              ? "Add a GitHub repository to get its first health score."
              : "An administrator needs to add repositories."}
          </EmptyState>
        ) : (
          <RepoTable
            repos={repos.data.items}
            isAdmin={isAdmin}
            sort={sort}
            order={order}
            onSort={onSort}
          />
        )}
      </section>

      {adding && isAdmin && <AddRepoModal onClose={closeAdd} />}
    </div>
  );
}
