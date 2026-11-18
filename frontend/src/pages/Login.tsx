import { type FormEvent, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError, errorMessage } from "../api/client";
import { useConfig } from "../api/hooks";
import { FieldError } from "../components/States";
import { useAuth } from "../hooks/useAuth";

interface LocationState {
  from?: string;
  notice?: string;
}

export function LoginPage() {
  const { login, token } = useAuth();
  const config = useConfig();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as LocationState;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (token) return <Navigate to={state.from ?? "/"} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    try {
      await login(email.trim(), password);
      navigate(state.from ?? "/", { replace: true });
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "TOO_MANY_ATTEMPTS"
          ? "Too many attempts. Wait a few minutes and try again."
          : errorMessage(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6" noValidate>
        <div className="text-center">
          <h1 className="text-2xl font-bold">GitWell</h1>
          <p className="text-sm text-zinc-500">Sign in to see how healthy your repositories are.</p>
        </div>
        {state.notice && (
          <p role="status" className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-800 dark:bg-green-950/40 dark:text-green-300">
            {state.notice}
          </p>
        )}
        <div>
          <label htmlFor="email" className="mb-1 block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
        </div>
        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <FieldError>{error}</FieldError>
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {config.data?.allow_registration && (
          <p className="text-center text-sm text-zinc-500">
            No account?{" "}
            <Link to="/register" className="text-blue-600 hover:underline dark:text-blue-400">
              Register
            </Link>
          </p>
        )}
      </form>
    </div>
  );
}
