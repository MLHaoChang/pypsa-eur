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
 *
 * With nothing remembered, tick EVERY applicable metric. The previous default
 * — first two series plus one scalar — meant arriving on a tab that had eight
 * results and seeing three, with no signal that the rest existed; the user
 * read it as the tab being half-empty. Showing everything and letting them
 * untick is the right way round: the data is already fetched either way, and
 * an untick is remembered from then on.
 */
export function reconcileSelection(
  remembered: string[] | null,
  metrics: MetricRow[],
): string[] {
  const ok = metrics.filter(m => m.status === 'ok')
  if (remembered) {
    const okIds = new Set(ok.map(m => m.id))
    const kept = remembered.filter(id => okIds.has(id))
    // A remembered set that survives as nothing (every id is now blocked or
    // n/a for this asset) would leave the panel blank with no explanation.
    // Treat it as "never configured" and fall through to the full default.
    if (kept.length > 0) return kept
  }
  return ok.map(m => m.id)
}

/** Every metric that resolved `ok` — what "Show all" restores. */
export const allApplicable = (metrics: MetricRow[]): string[] =>
  metrics.filter(m => m.status === 'ok').map(m => m.id)
