// Assistant-assurance 06 (A5.3, GK G1/G2): the one seam every href in assistant
// prose and on a chat card goes through. It is an allowlist: the href is
// resolved against this origin with the URL parser (so case, tabs, newlines,
// backslashes, `user@host` and protocol-relative forms are read the way the
// browser reads them) and is admitted only when it stays on this origin and its
// path is one of the app's own download or evidence routes. Guide links are
// buttons, not hrefs, and are checked by `guideLinkId` instead.

export type ChatLinkKind = 'upload' | 'answer' | 'export' | 'evidence'

const ROUTES: Record<ChatLinkKind, RegExp> = {
  upload: /^\/api\/projects\/[^/?#]+\/uploads\/[0-9a-f]{16}\/blob$/,
  answer: /^\/api\/chat\/answers\/[^/?#]+$/,
  export: /^\/api\/chat\/answers\/[^/?#]+\/export$/,
  evidence: /^\/api\/chat\/evidence\/[^/?#]+$/,
}

const ALL_KINDS = Object.keys(ROUTES) as ChatLinkKind[]

/** The same-origin path (with its query) to use as the href, or null. */
export function approvedChatHref(href: unknown, kinds: readonly ChatLinkKind[] = ALL_KINDS): string | null {
  if (typeof href !== 'string' || href === '' || href.length > 512) return null
  // A raw backslash or control character is never part of an app route; the
  // URL parser would otherwise turn `/\evil.example` into another host.
  if (/[\\\u0000-\u001f\u007f]/.test(href)) return null
  const origin = window.location.origin
  let url: URL
  try {
    url = new URL(href, origin)
  } catch {
    return null
  }
  if (url.origin !== origin || url.username !== '' || url.password !== '') return null
  if (!kinds.some(kind => ROUTES[kind].test(url.pathname))) return null
  return url.pathname + url.search
}
