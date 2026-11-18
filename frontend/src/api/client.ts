// A small fetch wrapper: bearer auth, the backend's error envelope, and 401 handling.

const DEFAULT_BASE = "http://localhost:8000/api/v1";
export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE).replace(
  /\/+$/,
  "",
);

const TOKEN_KEY = "gitwell.token";

// localStorage can throw (private windows, blocked storage): always fall back gracefully.
export const tokenStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* the session just won't survive a reload */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* nothing to clear */
    }
  },
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called when any authenticated request gets a 401 (the token is cleared first). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

type Query = Record<string, string | number | boolean | undefined | null>;

export interface RequestOptions {
  body?: unknown;
  query?: Query;
  /** Login/register are unauthenticated: a 401 there is just a wrong password. */
  auth?: boolean;
}

function buildUrl(path: string, query?: Query): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

interface ErrorEnvelope {
  error?: { code?: unknown; message?: unknown; details?: unknown };
}

export async function request<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, query, auth = true } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenStore.get();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the server. Check your connection.");
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body: handled below */
  }

  if (!response.ok) {
    const envelope = (payload ?? {}) as ErrorEnvelope;
    const err = envelope.error ?? {};
    const apiError = new ApiError(
      response.status,
      typeof err.code === "string" ? err.code : "UNKNOWN_ERROR",
      typeof err.message === "string" ? err.message : `Request failed (${response.status}).`,
      err.details && typeof err.details === "object" ? (err.details as Record<string, unknown>) : {},
    );
    if (response.status === 401 && auth) {
      tokenStore.clear();
      unauthorizedHandler?.();
    }
    throw apiError;
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown, auth = true) => request<T>("POST", path, { body, auth }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Human-readable text for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
