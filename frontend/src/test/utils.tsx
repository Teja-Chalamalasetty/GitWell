import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderOptions, render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../hooks/AuthProvider";

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", ...options }: { route?: string } & Omit<RenderOptions, "wrapper"> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    ...render(ui, {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[route]}>
            <AuthProvider>{children}</AuthProvider>
          </MemoryRouter>
        </QueryClientProvider>
      ),
      ...options,
    }),
  };
}

type Handler = (url: URL, init: RequestInit) => unknown;

export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message, details: {} } }, status);
}

/** Stub `fetch` with handlers keyed by "METHOD /path" (the API base is stripped). */
export function mockApi(routes: Record<string, Handler>) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({
      method,
      path,
      query: url.searchParams,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init.headers ?? {}) as Record<string, string>,
    });
    const handler = routes[`${method} ${path}`];
    if (!handler) return errorResponse(404, "NOT_FOUND", `no mock for ${method} ${path}`);
    const result = handler(url, init);
    return result instanceof Response ? result : jsonResponse(result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
