/**
 * Return a URL that is safe to expose as an external link.
 *
 * Discovery metadata is supplied by third-party providers, so do not rely on
 * its URL being trustworthy. In particular, only absolute HTTPS URLs are
 * eligible for navigation; relative, protocol-relative, and script URLs stay
 * plain text in the UI.
 */
export function safeExternalHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string" || !/^https:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}
