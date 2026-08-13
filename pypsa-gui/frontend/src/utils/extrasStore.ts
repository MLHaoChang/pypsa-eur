// ── Extras store ─────────────────────────────────────────────────────────────
// The list of attributes the user has added to a form beyond its curated set
// (spec D23). Pure over localStorage: no React, no DOM beyond the storage API.
//
// House convention from recon §18: ':' separator, feature-scoped namespace,
// dynamic segment last, every read and write individually try/catch-wrapped.
// The value carries its own version — `{ v: 1, keys: [...] }` — and a
// mismatched `v` drops the entry. Versioning lives INSIDE the value, never in
// the key (topologyLayoutStore.ts:19,41), so a future format change does not
// strand entries under an unreadable key.
//
// No regex sweep ships on project deletion: unlike network-diagram:*:state
// this family is not project-scoped and has nothing to clean up.

const VERSION = 1

/** D23's key: the creation form persists per palette type. */
export function creationScope(paletteId: string): string {
  return `creationform:extras:${paletteId}`
}

/**
 * The edit cards' key. D23 defines only the creation-form key, but D20 needs
 * extras on the edit cards too, where there is no palette id — a card knows
 * its component class. Same envelope, same rules.
 */
export function editScope(componentClass: string): string {
  return `propertiespanel:extras:${componentClass}`
}

export function loadExtras(scope: string): string[] {
  try {
    const raw = localStorage.getItem(scope)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return []
    const box = parsed as { v?: unknown; keys?: unknown }
    // A mismatched version drops the entry rather than guessing at its shape.
    if (box.v !== VERSION || !Array.isArray(box.keys)) return []
    const seen = new Set<string>()
    for (const k of box.keys) if (typeof k === 'string') seen.add(k)
    return [...seen]
  } catch {
    return []
  }
}

export function saveExtras(scope: string, keys: string[]): void {
  try {
    const unique = [...new Set(keys.filter(k => typeof k === 'string'))]
    localStorage.setItem(scope, JSON.stringify({ v: VERSION, keys: unique }))
  } catch {
    // Safari private mode and jsdom's bare {} both throw here. Losing the
    // preference is acceptable; losing the edit the user was making is not.
  }
}
