// =============================================================================
// CRS-01 time formatting — shared by the run-history panel, the researching
// panel, and the freshness lines on the Report/Sources views.
// All inputs are epoch SECONDS (the KB's timestamp convention).
// =============================================================================

/** Relative age ("just now" / "5m ago" / "3h ago" / "12d ago"). */
export function relativeTime(ts: number): string {
  if (!ts) return "—";
  const diff = Date.now() - ts * 1000;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/** Plain UTC date ("2026-08-19") for "First seen" lines. Empty when the
 * timestamp is absent — callers omit the segment rather than show a guess. */
export function formatDate(ts?: number | null): string {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toISOString().slice(0, 10);
  } catch {
    return "";
  }
}
