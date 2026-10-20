// Seconds since `active` last turned true, ticking once a second, for the
// "Studying… 0:41" lines (UX review L4/N16). Frontend clock only: it counts
// from when this view saw the run, not from the server's start time.
import { useEffect, useState } from 'react'

export function useElapsed(active: boolean): number {
  const [start, setStart] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) { setStart(null); return }
    const t0 = Date.now()
    setStart(t0)
    setNow(t0)
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return start == null ? 0 : Math.max(0, Math.floor((now - start) / 1000))
}

/** 41 → "0:41", 188 → "3:08". */
export function formatElapsed(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
