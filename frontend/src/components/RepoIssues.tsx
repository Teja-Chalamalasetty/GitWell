import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { useCreateDraft, useRepoDrafts } from "../api/hooks";
import type { RepoSummary } from "../api/types";
import { draftButtonState, safeGithubUrl } from "../lib/draft";
import { formatDateTime, relativeTime } from "../lib/format";
import { DraftStatusChip } from "./Badges";
import { EmptyState, ErrorState, FieldError, Spinner } from "./States";

/** The "GitHub issues" section of the repo page: draft button (admin) and this repo's drafts. */
export function RepoIssues({ repo, isAdmin }: { repo: RepoSummary; isAdmin: boolean }) {
  const navigate = useNavigate();
  const drafts = useRepoDrafts(repo.id);
  const create = useCreateDraft();
  const [error, setError] = useState<string | null>(null);
  const state = draftButtonState(repo);

  function draftIssue(scanId: number) {
    setError(null);
    create.mutate(scanId, {
      onSuccess: (draft) => navigate(`/issue-drafts/${draft.id}`),
      onError: (e) => setError(errorMessage(e)),
    });
  }

  return (
    <section aria-labelledby="issues-heading" className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="issues-heading" className="text-lg font-semibold">
          GitHub issues
        </h2>
        {isAdmin && state.kind === "link" && (
          <Link
            to={`/issue-drafts/${state.draftId}`}
            className="btn-secondary btn-sm"
          >
            Open the issue draft
          </Link>
        )}
        {isAdmin && state.kind === "button" && (
          <>
            <span title={state.disabled ? state.reason : undefined}>
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={state.disabled || create.isPending}
                onClick={() => !state.disabled && draftIssue(state.scanId)}
              >
                {create.isPending ? "Drafting…" : "Draft GitHub issue"}
              </button>
            </span>
            {state.disabled && <span className="text-sm text-zinc-500">{state.reason}</span>}
          </>
        )}
      </div>
      <FieldError>{error}</FieldError>

      {drafts.isPending ? (
        <Spinner />
      ) : drafts.isError ? (
        <ErrorState error={drafts.error} onRetry={() => void drafts.refetch()} />
      ) : drafts.data.items.length === 0 ? (
        <EmptyState title="No issue drafts yet">
          {isAdmin
            ? "Draft an issue from the latest results to ask the maintainers to fix what failed."
            : "Drafts created by administrators appear here."}
        </EmptyState>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[520px]">
            <thead className="border-b border-zinc-200 dark:border-zinc-800">
              <tr>
                <th className="th" scope="col">Title</th>
                <th className="th" scope="col">Status</th>
                <th className="th" scope="col">Created</th>
                <th className="th" scope="col">GitHub</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {drafts.data.items.map((draft) => {
                const url = safeGithubUrl(draft.github_issue_url);
                return (
                  <tr key={draft.id}>
                    <td className="td">
                      <Link
                        to={`/issue-drafts/${draft.id}`}
                        className="text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {draft.title}
                      </Link>
                    </td>
                    <td className="td">
                      <DraftStatusChip status={draft.status} />
                    </td>
                    <td className="td text-zinc-500" title={formatDateTime(draft.created_at)}>
                      {relativeTime(draft.created_at)}
                    </td>
                    <td className="td">
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 hover:underline dark:text-blue-400"
                        >
                          #{draft.github_issue_number} ↗
                        </a>
                      ) : (
                        <span className="text-zinc-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
