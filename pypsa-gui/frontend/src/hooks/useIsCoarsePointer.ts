import { useEffect, useState } from 'react'

/**
 * Reactive `matchMedia('(pointer: coarse)')` hook.
 *
 * Returns true on touch-first devices (tablets, phones) and false on
 * mouse/trackpad pointers. The value updates if the user docks a USB
 * mouse to a tablet mid-session — the `change` event on the media query
 * fires and we re-render.
 *
 * SSR-safe: if `window` isn't available, returns `false` so the desktop
 * path is the default rendering.
 */
export function useIsCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia('(pointer: coarse)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    const mq = window.matchMedia('(pointer: coarse)')
    const onChange = (e: MediaQueryListEvent) => setCoarse(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return coarse
}
