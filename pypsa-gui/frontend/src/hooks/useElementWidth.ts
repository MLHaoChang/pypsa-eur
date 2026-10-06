import { useEffect, useState } from 'react'

/**
 * The element's current content width in px, tracked with a ResizeObserver.
 * `null` until measured, or where ResizeObserver does not exist (jsdom):
 * callers must treat null as "unknown", not as zero.
 */
export function useElementWidth(el: HTMLElement | null): number | null {
  const [width, setWidth] = useState<number | null>(null)
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width
      if (w != null) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  return width
}
