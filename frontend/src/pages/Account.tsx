import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { useChangePassword } from "../api/hooks";
import { FieldError } from "../components/States";
import { useAuth } from "../hooks/useAuth";

export function AccountPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (next.length < 8 || next.length > 128) return setError("New password must be 8 to 128 characters.");
    if (next !== confirm) return setError("The new passwords do not match.");
    change.mutate(
      { current_password: current, new_password: next },
      {
        // The server revokes every old token when the password changes, so sign in again.
        onSuccess: () => {
          logout();
          navigate("/login", {
            replace: true,
            state: { notice: "Password changed. Please sign in again." },
          });
        },
        onError: (e) =>
          setError(
            e instanceof ApiError && e.status === 400
              ? "Your current password is incorrect."
              : errorMessage(e),
          ),
      },
    );
  }

  return (
    <div className="max-w-md space-y-6">
      <h1 className="text-2xl font-bold">Account</h1>
      <dl className="card space-y-2 p-4 text-sm">
        <div className="flex justify-between">
          <dt className="text-zinc-500">Email</dt>
          <dd>{user?.email}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-zinc-500">Role</dt>
          <dd className="capitalize">{user?.role}</dd>
        </div>
      </dl>

      <form onSubmit={submit} className="card space-y-4 p-4" noValidate aria-labelledby="pw-heading">
        <h2 id="pw-heading" className="text-sm font-semibold">
          Change password
        </h2>
        <div>
          <label htmlFor="current" className="mb-1 block text-sm font-medium">
            Current password
          </label>
          <input
            id="current"
            type="password"
            className="input"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new" className="mb-1 block text-sm font-medium">
            New password
          </label>
          <input
            id="new"
            type="password"
            className="input"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <p className="mt-1 text-xs text-zinc-500">8 to 128 characters.</p>
        </div>
        <div>
          <label htmlFor="confirm" className="mb-1 block text-sm font-medium">
            Confirm new password
          </label>
          <input
            id="confirm"
            type="password"
            className="input"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        <FieldError>{error}</FieldError>
        <button type="submit" className="btn-primary" disabled={change.isPending}>
          {change.isPending ? "Saving…" : "Change password"}
        </button>
        <p className="text-xs text-zinc-500">You will be signed out on every device afterwards.</p>
      </form>
    </div>
  );
}
