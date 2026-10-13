/**
 * Marking the numbers the audit could not find in the evidence (WP3's
 * `section.audit.unverified`, rendered by the WP7 viewer).
 *
 * `highlightUnverified` is the pure part: it splits a string into segments,
 * each flagged as unverified or not, matching a listed token only as a whole
 * (never "12" inside "120", "12.5" or "B12") and preferring the longest
 * token where two overlap. `rehypeMarkUnverified` applies that split to
 * every text node of the hast tree react-markdown renders from, wrapping the
 * unverified segments in `<mark title="not found in the evidence">` — so the
 * highlight survives inline markdown (a number inside `**bold**` still lights
 * up) without any raw-HTML pass-through.
 */

export const UNVERIFIED_TITLE = 'not found in the evidence'

export interface Segment { text: string; unverified: boolean }

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildMatcher(unverified: readonly string[]): RegExp | null {
  const tokens = Array.from(new Set(unverified.map(t => t.trim()).filter(Boolean)))
    .sort((a, b) => b.length - a.length)
  if (tokens.length === 0) return null
  const alt = tokens.map(escapeRegex).join('|')
  // A whole token: not glued to a letter, digit or underscore on either side,
  // and not the integer part / a decimal or thousands group of a longer
  // number ("12" in "12.5", "250" in "1,250").
  return new RegExp(
    `(?<![\\p{L}\\p{N}_])(?<!\\p{N}[.,])(?:${alt})(?![\\p{L}\\p{N}_])(?![.,]\\p{N})`,
    'gu',
  )
}

export function highlightUnverified(text: string, unverified: readonly string[]): Segment[] {
  if (!text) return []
  const re = buildMatcher(unverified)
  if (!re) return [{ text, unverified: false }]
  const out: Segment[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    const start = m.index ?? 0
    if (start > last) out.push({ text: text.slice(last, start), unverified: false })
    out.push({ text: m[0], unverified: true })
    last = start + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), unverified: false })
  return out
}

// ── the rehype plugin ───────────────────────────────────────────────────────
// Minimal structural view of hast — enough to walk and rewrite text nodes
// without importing the `hast` types package (a transitive dependency of
// react-markdown, not one of ours).

interface HastText { type: 'text'; value: string }
interface HastElement {
  type: 'element'
  tagName: string
  properties?: Record<string, unknown>
  children: HastNode[]
}
interface HastParent { type: string; children?: HastNode[] }
type HastNode = HastText | HastElement | HastParent

function markNode(text: string): HastElement {
  return {
    type: 'element',
    tagName: 'mark',
    properties: { title: UNVERIFIED_TITLE, className: ['report-unverified'] },
    children: [{ type: 'text', value: text }],
  }
}

function rewrite(node: HastNode, unverified: readonly string[]): void {
  const children = (node as HastParent).children
  if (!children) return
  // Code is quoted verbatim — a number inside a code span is not prose the
  // audit looked at.
  if ((node as HastElement).type === 'element' && (node as HastElement).tagName === 'code') return
  const next: HastNode[] = []
  for (const child of children) {
    if (child.type === 'text') {
      const segments = highlightUnverified((child as HastText).value, unverified)
      if (segments.length <= 1 && !segments[0]?.unverified) {
        next.push(child)
        continue
      }
      for (const s of segments) {
        next.push(s.unverified ? markNode(s.text) : { type: 'text', value: s.text })
      }
    } else {
      rewrite(child, unverified)
      next.push(child)
    }
  }
  ;(node as HastParent).children = next
}

/**
 * A rehype plugin factory: `rehypePlugins={[[rehypeMarkUnverified, unverified]]}`.
 * With an empty list it is a no-op transformer.
 */
export function rehypeMarkUnverified(unverified: readonly string[] = []) {
  return (tree: unknown) => {
    if (unverified.length === 0) return
    rewrite(tree as HastNode, unverified)
  }
}
