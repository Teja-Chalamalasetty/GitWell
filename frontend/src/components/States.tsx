import type { ReactNode } from "react";
import { errorMessage } from "../api/client";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-zinc-500" role="status">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-300 border-t-blue-600"
        aria-hidden
      />
      {label}
    </div>
  );
}

export function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner />
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-300 px-6 py-10 text-center dark:border-zinc-700">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-zinc-500">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
    >
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" className="btn-secondary btn-sm mt-2" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export type BannerTone = "info" | "warn" | "fail";

const BANNER: Record<BannerTone, string> = {
  info: "border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200",
  warn: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200",
  fail: "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200",
};

export function Banner({ tone, children }: { tone: BannerTone; children: ReactNode }) {
  return (
    <div role={tone === "fail" ? "alert" : "status"} className={`rounded-md border px-4 py-2 text-sm ${BANNER[tone]}`}>
      {children}
    </div>
  );
}

export function FieldError({ children }: { children?: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="mt-1 text-sm text-red-600 dark:text-red-400">
      {children}
    </p>
  );
}
