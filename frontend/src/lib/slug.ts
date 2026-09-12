// =============================================================================
// CRS-01 slug normalization — the frontend mirror of the backend's
// kb.normalize_slug: lowercase, trim, collapse any whitespace run into a
// single hyphen. One shared helper so links built client-side resolve to
// the same slugs the knowledge base stores.
// =============================================================================

export function normalizeSlug(name: string): string {
  return (name || "").trim().toLowerCase().replace(/\s+/g, "-");
}
