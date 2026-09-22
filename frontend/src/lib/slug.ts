// Filesystem/URL identity for a saved scenario (ADR-0026). Mirrors
// `registry.slugify` / `registry._SLUG_RE` in the backend — the server is the authority,
// this just keeps the UI honest before the request.

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;
const SLUG_MAX = 64;

/** Best-effort slug from a free-text scenario name. Returns "" when nothing usable is
 * left (the caller treats that as "not saveable yet"). */
export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX)
    .replace(/-+$/g, "");
}

export function isValidSlug(slug: string): boolean {
  return slug.length <= SLUG_MAX && SLUG_RE.test(slug);
}
