import { describe, expect, it } from "vitest";
import { formatDate, formatScore, relativeTime, shortSha } from "./format";

const NOW = Date.parse("2026-11-18T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it.each([
    [10, "just now"],
    [44, "just now"],
    [120, "2 minutes ago"],
    [3600, "1 hour ago"],
    [3 * 3600, "3 hours ago"],
    [24 * 3600, "yesterday"],
    [3 * 24 * 3600, "3 days ago"],
    [14 * 24 * 3600, "2 weeks ago"],
    [60 * 24 * 3600, "2 months ago"],
  ])("%is ago -> %s", (seconds, expected) => {
    expect(relativeTime(ago(seconds), NOW)).toBe(expected);
  });

  it("handles missing and invalid input", () => {
    expect(relativeTime(null, NOW)).toBe("never");
    expect(relativeTime(undefined, NOW)).toBe("never");
    expect(relativeTime("garbage", NOW)).toBe("unknown");
  });

  it("handles times in the future", () => {
    expect(relativeTime(new Date(NOW + 2 * 3600 * 1000).toISOString(), NOW)).toBe("in 2 hours");
  });
});

describe("small formatters", () => {
  it("shortSha", () => {
    expect(shortSha("abcdef1234567890")).toBe("abcdef1");
    expect(shortSha(null)).toBe("—");
  });
  it("formatScore", () => {
    expect(formatScore(0)).toBe("0");
    expect(formatScore(null)).toBe("—");
    expect(formatScore(undefined)).toBe("—");
  });
  it("formatDate keeps the UTC day", () => {
    expect(formatDate("2026-11-18")).toMatch(/Nov/);
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });
});
