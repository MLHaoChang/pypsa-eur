/**
 * Links into the Parameter guide (spec expert-catalogue §6.1): `guide:<id>`, where the
 * id is a catalogue guide id (`generator.capacity`, `function.load_flow`). One pattern
 * for every place that reads such a link: the chat's URL filter and its click
 * handler, and the guide's own markdown.
 */

/** `guide:` then a lower-case word and one or more `.`-separated parts; nothing else. */
export const GUIDE_LINK_RE = /^guide:([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)$/

/** The guide id a link points at, or null when it is not exactly a guide link. */
export function guideLinkId(href: unknown): string | null {
  if (typeof href !== 'string') return null
  return GUIDE_LINK_RE.exec(href)?.[1] ?? null
}

/**
 * URL filter for guide markdown: a guide link (as a link target only), or an absolute
 * `http:`/`https:` URL, read with the URL parser so case, leading spaces and entity
 * tricks cannot hide another scheme. Everything else (relative, protocol-relative,
 * `javascript:`, `data:`, `vbscript:`) becomes '' and renders as plain text.
 */
export function guideUrlTransform(url: string, key: string): string {
  if (guideLinkId(url)) return key === 'href' ? url : ''
  let protocol: string
  try { protocol = new URL(url).protocol } catch { return '' }
  return protocol === 'http:' || protocol === 'https:' ? url : ''
}

/** An absolute web link (opens in a new tab). */
export function isWebUrl(href: unknown): boolean {
  if (typeof href !== 'string' || !href) return false
  try {
    const { protocol } = new URL(href)
    return protocol === 'http:' || protocol === 'https:'
  } catch { return false }
}
