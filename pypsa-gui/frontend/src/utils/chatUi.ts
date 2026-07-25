/** Pure helpers for ChatPanel scroll / markdown affordances (Track A). */

/** True when the scroll container is within `thresholdPx` of the bottom. */
export function isNearBottom(
  el: { scrollHeight: number; scrollTop: number; clientHeight: number },
  thresholdPx = 80,
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx
}

/** Flatten React children / markdown nodes into plain text for clipboard copy. */
export function extractCopyText(node: unknown): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractCopyText).join('')
  if (typeof node === 'object' && node !== null && 'props' in node) {
    const props = (node as { props?: { children?: unknown } }).props
    return extractCopyText(props?.children)
  }
  return ''
}
