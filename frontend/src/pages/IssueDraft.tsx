import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage } from "../api/client";
import {
  useApproveDraft,
  useConfig,
  useDiscardDraft,
  useIssueDraft,
  useUpdateDraft,
} from "../api/hooks";
import type { IssueDraft } from "../api/types";
import { DraftStatusChip } from "../components/Badges";
import { Markdown } from "../components/Markdown";
import { ConfirmDialog } from "../components/Modal";
import { Banner, ErrorState, FieldError, Spinner } from "../components/States";
import { useAuth } from "../hooks/useAuth";
import { formatDateTime } from "../lib/format";
import { safeGithubUrl } from "../lib/draft";
import { NotFoundPage } from "./NotFound";

const TITLE_MAX = 256;
const BODY_MAX = 60_000;

export function IssueDraftPage() {
  const params = useParams();
  const draftId = Number(params.id);
  if (!Number.isInteger(draftId) || draftId <= 0) return <NotFoundPage />;
  return <IssueDraftView draftId={draftId} />;
}

function IssueDraftView({ draftId }: { draftId: number }) {
  const draft = useIssueDraft(draftId);

  if (draft.isPending) return <Spinner label="Loading draft…" />;
  if (draft.isError || !draft.data) {
    return <ErrorState error={draft.error ?? new Error("Not found")} onRetry={() => void draft.refetch()} />;
  }
  // A status change (approved, posted, discarded) remounts the screen into its new mode.
  const d = draft.data;
  return <DraftScreen key={`${d.id}:${d.status}`} draft={d} />;
}

function DraftScreen({ draft }: { draft: IssueDraft }) {
  const { isAdmin } = useAuth();
  const config = useConfig();
  const approve = useApproveDraft(draft.id);
  const discard = useDiscardDraft(draft.id);
  const [confirming, setConfirming] = useState<"approve" | "discard" | null>(null);
  const [dirty, setDirty] = useState(false);

  const issueUrl = safeGithubUrl(draft.github_issue_url);
  const canPost = config.data?.github_token_configured ?? false;
  const editable = isAdmin && draft.status === "drafted";
  const visibility = draft.repo_is_private ? "private" : "public";

  function closeDialog() {
    setConfirming(null);
    approve.reset();
    discard.reset();
  }

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-sm text-zinc-500">
        <Link to="/" className="hover:underline">
          Dashboard
        </Link>{" "}
        /{" "}
        <Link to={`/repos/${draft.repo_id}`} className="hover:underline">
          {draft.repo_full_name}
        </Link>{" "}
        / <span>Issue draft #{draft.id}</span>
      </nav>

      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Issue draft</h1>
        <DraftStatusChip status={draft.status} />
        <Link
          to={`/scans/${draft.scan_id}`}
          className="ml-auto text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          View the scan this is based on
        </Link>
      </header>

      {draft.status === "drafted" && (
        <Banner tone="info">
          Nothing has been posted. Review and edit this issue, then approve it to post it to{" "}
          <strong>{draft.repo_full_name}</strong> on GitHub.
        </Banner>
      )}
      {draft.status === "approved" && (
        <Banner tone="warn">
          <strong>This issue was approved but not posted.</strong>{" "}
          {draft.last_error ?? "The post did not complete."} Retrying is safe: GitWell checks for
          an issue it already created before posting again.
        </Banner>
      )}
      {draft.status === "posting" && (
        <Banner tone="info">Posting to GitHub… this page updates by itself.</Banner>
      )}
      {draft.status === "posted" && (
        <Banner tone="info">
          <strong>Posted.</strong>{" "}
          {issueUrl ? (
            <a
              href={issueUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium underline"
            >
              Open issue #{draft.github_issue_number ?? ""} on GitHub ↗
            </a>
          ) : (
            "The issue was created on GitHub."
          )}
        </Banner>
      )}
      {draft.status === "discarded" && (
        <Banner tone="warn">This draft was discarded. Nothing was posted.</Banner>
      )}
      {!isAdmin && draft.status !== "posted" && draft.status !== "discarded" && (
        <p className="text-sm text-zinc-500">Only administrators can edit or post drafts.</p>
      )}

      {editable ? (
        <Editor draft={draft} onDirtyChange={setDirty} />
      ) : (
        <ReadOnly draft={draft} />
      )}

      {isAdmin && (draft.status === "drafted" || draft.status === "approved") && (
        <div className="card flex flex-wrap items-center gap-3 p-4">
          <button
            type="button"
            className="btn-primary"
            disabled={!canPost || dirty}
            onClick={() => setConfirming("approve")}
          >
            {draft.status === "approved" ? "Retry posting" : "Approve & post"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setConfirming("discard")}
          >
            Discard
          </button>
          <p className="text-sm text-zinc-500">
            {config.isPending
              ? "Checking the server's GitHub settings…"
              : !canPost
                ? "Posting is disabled: no GitHub token is configured on the server."
                : dirty
                  ? "Save your changes before posting."
                  : `Approving creates a ${visibility} issue on ${draft.repo_full_name}.`}
          </p>
        </div>
      )}

      {confirming === "approve" && (
        <ConfirmDialog
          title={draft.status === "approved" ? "Retry posting?" : "Post this issue?"}
          confirmLabel={approve.isPending ? "Posting…" : "Post to GitHub"}
          busy={approve.isPending}
          error={approve.error ? errorMessage(approve.error) : null}
          onCancel={closeDialog}
          onConfirm={() =>
            approve.mutate(undefined, { onSuccess: () => setConfirming(null) })
          }
        >
          <p>
            This will create a <strong>{visibility}</strong> issue on{" "}
            <strong>{draft.repo_full_name}</strong>
            {draft.repo_is_private
              ? ", visible to people with access to that repository."
              : ", visible to anyone on the internet."}
          </p>
          <p className="mt-2 font-medium">{draft.title}</p>
        </ConfirmDialog>
      )}

      {confirming === "discard" && (
        <ConfirmDialog
          title="Discard this draft?"
          confirmLabel="Discard"
          busy={discard.isPending}
          error={discard.error ? errorMessage(discard.error) : null}
          onCancel={closeDialog}
          onConfirm={() => discard.mutate(undefined, { onSuccess: () => setConfirming(null) })}
        >
          <p>The draft is closed and nothing is posted. You can draft a new one from the repository page.</p>
        </ConfirmDialog>
      )}

      <p className="text-xs text-zinc-500">
        Created {formatDateTime(draft.created_at)}
        {draft.approved_at && <> · approved {formatDateTime(draft.approved_at)}</>}
        {draft.post_attempts > 0 && <> · {draft.post_attempts} post attempt(s)</>}
      </p>
    </div>
  );
}

function ReadOnly({ draft }: { draft: IssueDraft }) {
  return (
    <section className="card space-y-3 p-4" aria-label="Issue contents">
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Title</h2>
        <p className="mt-1 text-lg font-semibold">{draft.title}</p>
      </div>
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Body</h2>
        <div className="mt-1">
          <Markdown>{draft.body}</Markdown>
        </div>
      </div>
    </section>
  );
}

function Editor({
  draft,
  onDirtyChange,
}: {
  draft: IssueDraft;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const update = useUpdateDraft(draft.id);
  const [title, setTitle] = useState(draft.title);
  const [body, setBody] = useState(draft.body);
  const [saved, setSaved] = useState(false);

  const titleChanged = title !== draft.title;
  const bodyChanged = body !== draft.body;
  const dirty = titleChanged || bodyChanged;
  const problem =
    title.trim() === ""
      ? "The title cannot be empty."
      : title.length > TITLE_MAX
        ? `The title can be at most ${TITLE_MAX} characters.`
        : body.trim() === ""
          ? "The body cannot be empty."
          : body.length > BODY_MAX
            ? `The body can be at most ${BODY_MAX.toLocaleString()} characters.`
            : null;

  function change(setter: (value: string) => void, value: string) {
    setter(value);
    setSaved(false);
    update.reset();
    // Computed against the new value so the parent never lags a keystroke behind.
    const nextTitle = setter === setTitle ? value : title;
    const nextBody = setter === setBody ? value : body;
    onDirtyChange(nextTitle !== draft.title || nextBody !== draft.body);
  }

  function save() {
    if (problem) return;
    const changes: { title?: string; body?: string } = {};
    if (titleChanged) changes.title = title.trim();
    if (bodyChanged) changes.body = body;
    update.mutate(changes, {
      onSuccess: (updated) => {
        setTitle(updated.title); // the server trims the title
        setBody(updated.body);
        setSaved(true);
        onDirtyChange(false);
      },
    });
  }

  return (
    <section className="grid gap-4 lg:grid-cols-2" aria-label="Edit issue">
      <div className="card space-y-3 p-4">
        <div>
          <label htmlFor="draft-title" className="mb-1 block text-sm font-medium">
            Title
          </label>
          <input
            id="draft-title"
            className="input"
            value={title}
            onChange={(e) => change(setTitle, e.target.value)}
            aria-invalid={problem !== null && title.trim() === "" ? true : undefined}
          />
        </div>
        <div>
          <label htmlFor="draft-body" className="mb-1 block text-sm font-medium">
            Body (Markdown)
          </label>
          <textarea
            id="draft-body"
            className="input min-h-80 font-mono text-xs"
            value={body}
            onChange={(e) => change(setBody, e.target.value)}
          />
          <p className="mt-1 text-xs text-zinc-500">
            {body.length.toLocaleString()} / {BODY_MAX.toLocaleString()} characters
          </p>
        </div>
        <FieldError>{dirty ? problem : null}</FieldError>
        <FieldError>{update.error ? errorMessage(update.error) : null}</FieldError>
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="btn-secondary"
            onClick={save}
            disabled={!dirty || problem !== null || update.isPending}
          >
            {update.isPending ? "Saving…" : "Save"}
          </button>
          {saved && !dirty && (
            <span role="status" className="text-sm text-green-700 dark:text-green-400">
              Saved.
            </span>
          )}
        </div>
      </div>

      <div className="card space-y-3 p-4" aria-label="Preview">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Preview
        </h2>
        <p className="text-lg font-semibold">{title}</p>
        <Markdown>{body}</Markdown>
      </div>
    </section>
  );
}
