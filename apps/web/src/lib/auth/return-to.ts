/** Where to go after sign-in: only a path on this console's own origin, never another site.
 * Browsers read "/\evil.example" like "//evil.example", so a prefix check alone isn't enough;
 * resolve against our origin and compare origins instead. */
export function safeReturnTo(to: string | null | undefined, origin: string, fallback = "/apps"): string {
  if (!to || !to.startsWith("/")) return fallback;
  try {
    const url = new URL(to, origin);
    if (url.origin !== origin) return fallback;
    return url.pathname + url.search + url.hash;
  } catch {
    return fallback;
  }
}
