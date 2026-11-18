import { type FormEvent, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api/client";
import { useConfig } from "../api/hooks";
import { FieldError, FullPageSpinner } from "../components/States";

export function RegisterPage() {
  const config = useConfig();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (config.isPending) return <FullPageSpinner />;
  // Registration is off by default; there is nothing to show when it is disabled.
  if (!config.data?.allow_registration) return <Navigate to="/login" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password.length < 8 || password.length > 128) {
      setError("Password must be 8 to 128 characters.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/register", { email: email.trim(), password }, false);
      // No auto-login: send them to sign in.
      navigate("/login", { replace: true, state: { notice: "Account created. Please sign in." } });
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "VALIDATION_ERROR"
          ? "Enter a valid email and a password of 8 to 128 characters."
          : errorMessage(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6" noValidate>
        <h1 className="text-center text-2xl font-bold">Create an account</h1>
        <div>
          <label htmlFor="reg-email" className="mb-1 block text-sm font-medium">
            Email
          </label>
          <input
            id="reg-email"
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
        </div>
        <div>
          <label htmlFor="reg-password" className="mb-1 block text-sm font-medium">
            Password
          </label>
          <input
            id="reg-password"
            className="input"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="mt-1 text-xs text-zinc-500">8 to 128 characters.</p>
        </div>
        <FieldError>{error}</FieldError>
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
        <p className="text-center text-sm text-zinc-500">
          Already registered?{" "}
          <Link to="/login" className="text-blue-600 hover:underline dark:text-blue-400">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
