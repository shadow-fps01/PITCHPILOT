/** Minimal class-name joiner (no external dependency). */
export function cn(
  ...classes: Array<string | false | null | undefined>
): string {
  return classes.filter(Boolean).join(" ");
}

/** 78 → "1:18" */export function formatDuration(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/** ISO timestamp → "2m ago" / "3h ago" / "2d ago" */
export function formatTimeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSeconds = Math.max(0, (Date.now() - then) / 1000);
  if (diffSeconds < 60) return "just now";
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
  return `${Math.floor(diffSeconds / 86400)}d ago`;
}

/** ISO timestamp → "Nov 15, 2026" (locale-aware, UTC-safe) */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Read a single-use auth token from the URL fragment (`#verify=...`), falling
 * back to the legacy query-string form (`?verify=...`). Fragments are never
 * sent to the server, so they stay out of proxy and access logs.
 */
export function readUrlToken(name: string): string | null {
  const rawHash = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  const fromHash = new URLSearchParams(rawHash).get(name);
  if (fromHash !== null && fromHash.length > 0) return fromHash;
  const fromQuery = new URLSearchParams(window.location.search).get(name);
  return fromQuery !== null && fromQuery.length > 0 ? fromQuery : null;
}

/** Remove an auth token from both the fragment and the legacy query string. */
export function clearUrlToken(name: string): void {
  const rawHash = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  const hashParams = new URLSearchParams(rawHash);
  hashParams.delete(name);
  const queryParams = new URLSearchParams(window.location.search);
  queryParams.delete(name);

  const query = queryParams.toString();
  const hash = hashParams.toString();
  window.history.replaceState(
    {},
    "",
    `${window.location.pathname}${query.length > 0 ? `?${query}` : ""}${
      hash.length > 0 ? `#${hash}` : ""
    }`,
  );
}

/**
 * Only allow http(s) links from untrusted content (copilot web sources).
 * Returns null for `javascript:`, `data:` and malformed URLs.
 */
export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}
