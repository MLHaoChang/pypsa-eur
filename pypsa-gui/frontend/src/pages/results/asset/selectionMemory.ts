import type { MetricRow } from './types'

const key = (cls: string, category: string) => `assetDetail:metrics:${cls}:${category}`

/** Remembered tick-set, or null when this (class, category) has never been
 *  configured — callers fall back to a computed default. */
export function loadSelection(cls: string, category: string): string[] | null {
  try {
    const raw = localStorage.getItem(key(cls, category))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) && parsed.every(x => typeof x === 'string')
      ? parsed : null
  } catch { return null }
}

export function saveSelection(cls: string, category: string, ids: string[]): void {
  try { localStorage.setItem(key(cls, category), JSON.stringify(ids)) }
  catch { /* quota or private mode — the tab still works, it just forgets */ }
}

/**
 * Reconcile a remembered tick-set against the metrics the backend actually
 * resolved for THIS asset.
 *
 * Remembered ids that are gone, blocked or n/a are dropped silently — their
 * reason is already visible in the checklist, so a toast would be noise.
 * With nothing remembered, default to the first two `ok` series plus the
 * first `ok` scalar: enough to show something useful, few enough to read.
 */
export function reconcileSelection(
  remembered: string[] | null,
  metrics: MetricRow[],
): string[] {
  const ok = new Set(metrics.filter(m => m.status === 'ok').map(m => m.id))
  if (remembered) return remembered.filter(id => ok.has(id))
  const series = metrics.filter(m => m.status === 'ok' && m.kind === 'series')
  const scalars = metrics.filter(m => m.status === 'ok' && m.kind === 'scalar')
  return [...series.slice(0, 2), ...scalars.slice(0, 1)].map(m => m.id)
}
