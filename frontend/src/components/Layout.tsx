import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { ErrorState, FullPageSpinner } from "./States";

/** Wraps every page that needs a signed-in user. */
export function RequireAuth() {
  const { token, user, isLoading, error, logout } = useAuth();
  const location = useLocation();

  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (isLoading) return <FullPageSpinner />;
  if (!user) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <ErrorState error={error ?? new Error("Could not load your account.")} onRetry={logout} />
      </div>
    );
  }
  return <Layout />;
}

/** Pages only admins may open (the server enforces this too). */
export function RequireAdmin() {
  const { isAdmin } = useAuth();
  return isAdmin ? <Outlet /> : <Navigate to="/" replace />;
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive
      ? "bg-zinc-200 dark:bg-zinc-800"
      : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800/60"
  }`;

function Layout() {
  const { user, isAdmin, logout } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-2 px-4 py-2">
          <NavLink to="/" className="mr-4 flex items-center gap-2 text-lg font-bold">
            <span
              className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-blue-600 text-white"
              aria-hidden
            >
              ✓
            </span>
            GitWell
          </NavLink>
          <nav aria-label="Main" className="flex flex-wrap items-center gap-1">
            <NavLink to="/" end className={linkClass}>
              Dashboard
            </NavLink>
            {isAdmin && (
              <NavLink to="/users" className={linkClass}>
                Users
              </NavLink>
            )}
            <NavLink to="/account" className={linkClass}>
              Account
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-zinc-500 sm:inline" title={user?.role}>
              {user?.email}
              {isAdmin && (
                <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs font-semibold text-blue-800 dark:bg-blue-900/40 dark:text-blue-300">
                  admin
                </span>
              )}
            </span>
            <button type="button" className="btn-secondary btn-sm" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
