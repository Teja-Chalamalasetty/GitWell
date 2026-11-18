import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { useAddRepo } from "../api/hooks";
import { validateRepoUrl } from "../lib/repoUrl";
import { Modal } from "./Modal";
import { FieldError } from "./States";

export function AddRepoModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const add = useAddRepo();
  const [url, setUrl] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);

  const serverError = add.error
    ? add.error instanceof ApiError && add.error.code === "GITHUB_RATE_LIMITED"
      ? "GitHub's rate limit was reached. Try again in a few minutes."
      : errorMessage(add.error)
    : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    // The same strict check as the server runs first; the server's answer is still shown.
    const problem = validateRepoUrl(url);
    setClientError(problem);
    if (problem) return;
    add.mutate(url.trim(), {
      onSuccess: (repo) => {
        onClose();
        navigate(`/repos/${repo.id}`);
      },
    });
  }

  return (
    <Modal title="Add repository" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <label htmlFor="repo-url" className="mb-1 block text-sm font-medium">
          GitHub repository URL
        </label>
        <input
          id="repo-url"
          className="input"
          type="url"
          inputMode="url"
          autoComplete="off"
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setClientError(null);
            add.reset();
          }}
          aria-invalid={clientError || serverError ? true : undefined}
          aria-describedby="repo-url-error"
        />
        <div id="repo-url-error">
          <FieldError>{clientError ?? serverError}</FieldError>
        </div>
        <p className="mt-2 text-xs text-zinc-500">
          Public repositories, and private ones the server's GitHub token can read. The first scan
          starts straight away.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={add.isPending}>
            {add.isPending ? "Adding…" : "Add repository"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
