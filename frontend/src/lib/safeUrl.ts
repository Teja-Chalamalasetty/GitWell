// PRD S13: only http, https and mailto links survive.
const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

export function safeUrl(url: string): string {
  const trimmed = url.trim();
  // Relative links and in-page anchors are harmless.
  if (/^(?:#|\/|\.\/|\.\.\/)/.test(trimmed)) return trimmed;
  try {
    return SAFE_PROTOCOLS.has(new URL(trimmed).protocol) ? trimmed : "";
  } catch {
    return "";
  }
}
