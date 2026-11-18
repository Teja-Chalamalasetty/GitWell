import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, errorMessage, setUnauthorizedHandler, tokenStore } from "./client";
import { errorResponse, jsonResponse, mockApi } from "../test/utils";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe("request", () => {
  it("sends the bearer token and parses JSON", async () => {
    tokenStore.set("tok123");
    const { calls, fetchMock } = mockApi({ "GET /repos": () => ({ items: [], total: 0 }) });
    await expect(api.get("/repos")).resolves.toEqual({ items: [], total: 0 });
    expect(calls).toHaveLength(1);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok123");
  });

  it("builds a query string and skips undefined values", async () => {
    const { calls } = mockApi({ "GET /repos": () => ({}) });
    await api.get("/repos", { sort: "score", order: "desc", limit: 200, status: undefined });
    expect(calls[0]?.query.get("sort")).toBe("score");
    expect(calls[0]?.query.get("limit")).toBe("200");
    expect(calls[0]?.query.has("status")).toBe(false);
  });

  it("sends JSON bodies with a content type", async () => {
    const { calls, fetchMock } = mockApi({ "POST /repos": () => ({ id: 1 }) });
    await api.post("/repos", { url: "https://github.com/o/r" });
    expect(calls[0]?.body).toEqual({ url: "https://github.com/o/r" });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("returns undefined for 204", async () => {
    mockApi({ "DELETE /repos/1": () => new Response(null, { status: 204 }) });
    await expect(api.delete("/repos/1")).resolves.toBeUndefined();
  });

  it("turns the error envelope into an ApiError", async () => {
    mockApi({
      "POST /repos": () =>
        jsonResponse(
          { error: { code: "REPO_ALREADY_EXISTS", message: "Already tracked.", details: { id: 4 } } },
          409,
        ),
    });
    const error = await api.post("/repos", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "REPO_ALREADY_EXISTS",
      message: "Already tracked.",
      details: { id: 4 },
    });
  });

  it("copes with an error response that is not JSON", async () => {
    mockApi({ "GET /x": () => new Response("<html>bad gateway</html>", { status: 502 }) });
    const error = (await api.get("/x").catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 502, code: "UNKNOWN_ERROR" });
    expect(error.message).toBe("Request failed (502).");
  });

  it("reports a network failure as NETWORK_ERROR", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = (await api.get("/x").catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 0, code: "NETWORK_ERROR" });
  });
});

describe("401 handling", () => {
  it("clears the token and calls the handler", async () => {
    tokenStore.set("expired");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({ "GET /repos": () => errorResponse(401, "UNAUTHENTICATED", "Invalid or expired token.") });
    await expect(api.get("/repos")).rejects.toMatchObject({ status: 401 });
    expect(tokenStore.get()).toBeNull();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not treat a wrong password on login as an expired session", async () => {
    tokenStore.set("still-valid");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({
      "POST /auth/login": () => errorResponse(401, "INVALID_CREDENTIALS", "Invalid email or password."),
    });
    await expect(api.post("/auth/login", {}, false)).rejects.toMatchObject({
      code: "INVALID_CREDENTIALS",
    });
    expect(handler).not.toHaveBeenCalled();
    expect(tokenStore.get()).toBe("still-valid");
  });

  it("does not send the token on unauthenticated calls", async () => {
    tokenStore.set("tok");
    const { fetchMock } = mockApi({ "POST /auth/login": () => ({}) });
    await api.post("/auth/login", {}, false);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("other errors do not log the user out", async () => {
    tokenStore.set("tok");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({ "POST /repos": () => errorResponse(403, "FORBIDDEN", "Administrator access required.") });
    await expect(api.post("/repos", {})).rejects.toMatchObject({ status: 403 });
    expect(handler).not.toHaveBeenCalled();
    expect(tokenStore.get()).toBe("tok");
  });
});

describe("tokenStore", () => {
  it("survives storage that throws", () => {
    const original = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(tokenStore.get()).toBeNull();
    expect(() => tokenStore.set("x")).not.toThrow();
    expect(() => tokenStore.clear()).not.toThrow();
    vi.restoreAllMocks();
    expect(Storage.prototype.getItem).toBe(original);
  });
});

describe("errorMessage", () => {
  it("handles every kind of thrown value", () => {
    expect(errorMessage(new ApiError(400, "X", "api message"))).toBe("api message");
    expect(errorMessage(new Error("plain"))).toBe("plain");
    expect(errorMessage("weird")).toBe("Something went wrong.");
  });
});
