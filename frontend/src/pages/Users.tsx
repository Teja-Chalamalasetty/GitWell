import { type FormEvent, useState } from "react";
import { errorMessage } from "../api/client";
import { useCreateUser, useUpdateUser, useUsers } from "../api/hooks";
import type { Role, User } from "../api/types";
import { Chip } from "../components/Badges";
import { EmptyState, ErrorState, FieldError, Spinner } from "../components/States";
import { useAuth } from "../hooks/useAuth";
import { formatDateTime } from "../lib/format";

function CreateUserForm() {
  const create = useCreateUser();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [localError, setLocalError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    setLocalError(null);
    if (!email.trim()) return setLocalError("Enter an email address.");
    if (password.length < 8 || password.length > 128) {
      return setLocalError("Password must be 8 to 128 characters.");
    }
    create.mutate(
      { email: email.trim(), password, role },
      {
        onSuccess: () => {
          setEmail("");
          setPassword("");
          setRole("viewer");
        },
      },
    );
  }

  return (
    <form onSubmit={submit} className="card space-y-3 p-4" noValidate aria-labelledby="create-user">
      <h2 id="create-user" className="text-sm font-semibold">
        Create user
      </h2>
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto]">
        <div>
          <label htmlFor="new-email" className="mb-1 block text-xs font-medium">
            Email
          </label>
          <input
            id="new-email"
            type="email"
            className="input"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new-password" className="mb-1 block text-xs font-medium">
            Temporary password
          </label>
          <input
            id="new-password"
            type="password"
            className="input"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new-role" className="mb-1 block text-xs font-medium">
            Role
          </label>
          <select
            id="new-role"
            className="input"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            <option value="viewer">Viewer</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <div className="flex items-end">
          <button type="submit" className="btn-primary w-full" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
      <FieldError>{localError ?? (create.error ? errorMessage(create.error) : null)}</FieldError>
      {create.isSuccess && (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          User created.
        </p>
      )}
    </form>
  );
}

function UserRow({ user, isSelf }: { user: User; isSelf: boolean }) {
  const update = useUpdateUser();
  // The server's answer (e.g. LAST_ADMIN) is shown inline, next to the row it concerns.
  const error = update.error ? errorMessage(update.error) : null;
  return (
    <>
      <tr>
        <td className="td">
          {user.email}
          {isSelf && (
            <span className="ml-2">
              <Chip tone="info">You</Chip>
            </span>
          )}
        </td>
        <td className="td">
          <select
            className="input w-auto py-1"
            aria-label={`Role for ${user.email}`}
            value={user.role}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ id: user.id, role: e.target.value as Role })}
          >
            <option value="viewer">Viewer</option>
            <option value="admin">Admin</option>
          </select>
        </td>
        <td className="td">
          <label className="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              role="switch"
              checked={user.is_active}
              disabled={update.isPending}
              onChange={(e) => update.mutate({ id: user.id, is_active: e.target.checked })}
              aria-label={`Active: ${user.email}`}
            />
            <span className="text-sm">{user.is_active ? "Active" : "Disabled"}</span>
          </label>
        </td>
        <td className="td text-zinc-500">{formatDateTime(user.created_at)}</td>
      </tr>
      {error && (
        <tr>
          <td colSpan={4} className="px-3 pb-2">
            <FieldError>{error}</FieldError>
          </td>
        </tr>
      )}
    </>
  );
}

export function UsersPage() {
  const { user: me } = useAuth();
  const users = useUsers();
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Users</h1>
      <CreateUserForm />
      {users.isPending ? (
        <Spinner label="Loading users…" />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => void users.refetch()} />
      ) : users.data.items.length === 0 ? (
        <EmptyState title="No users" />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead className="border-b border-zinc-200 dark:border-zinc-800">
              <tr>
                <th className="th" scope="col">Email</th>
                <th className="th" scope="col">Role</th>
                <th className="th" scope="col">Status</th>
                <th className="th" scope="col">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {users.data.items.map((u) => (
                <UserRow key={u.id} user={u} isSelf={u.id === me?.id} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
