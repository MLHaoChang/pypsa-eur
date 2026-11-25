// The Expert island editor's save, once a sidecar exists: a PATCH of the
// requirements that changed, as plain values (control-strategy 02, GK B3 K4,
// K19). The server sets every source and refuses a client-sent one at any
// depth, so a stored `{value, source}` is never sent back: only the leaves
// that differ from the loaded config, and a source edited in the raw text is
// refused here, in words, before any request.

export interface RequirementsDiff {
  /** The `requirements` PATCH; empty when nothing changed. */
  patch: Record<string, unknown>
  /** Edits the PATCH cannot carry, each as `path: words`. */
  refusals: string[]
}

type Json = unknown
const isObj = (v: Json): v is Record<string, Json> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isTagged = (v: Json): v is { value: Json; source: Json } =>
  isObj(v) && Object.keys(v).length === 2 && 'value' in v && 'source' in v
const same = (a: Json, b: Json) => JSON.stringify(a) === JSON.stringify(b)

function diffInto(path: string, saved: Record<string, Json>, edited: Record<string, Json>, out: RequirementsDiff,
  patch: Record<string, unknown>): void {
  for (const k of Object.keys(saved)) {
    if (!(k in edited)) out.refusals.push(`${path}${k}: a setting cannot be removed here`)
  }
  for (const [k, next] of Object.entries(edited)) {
    const where = `${path}${k}`
    const old = saved[k]
    if (isTagged(old)) {
      if (isTagged(next)) {
        if (!same(next.source, old.source)) out.refusals.push(`${where}: sources are set by the server, not here`)
        if (!same(next.value, old.value)) patch[k] = next.value
      } else if (isObj(next)) {
        out.refusals.push(`${where}: enter the value only`)
      } else if (!same(next, old.value)) {
        patch[k] = next
      }
    } else if (isObj(old) && isObj(next) && !isTagged(next)) {
      const sub: Record<string, unknown> = {}
      diffInto(`${where}.`, old, next, out, sub)
      if (Object.keys(sub).length) patch[k] = sub
    } else if (isTagged(next) || (isObj(next) && 'source' in next)) {
      out.refusals.push(`${where}: sources are set by the server, not here`)
    } else if (next === null) {
      out.refusals.push(`${where}: a setting cannot be removed here`)
    } else if (!same(next, old)) {
      patch[k] = next
    }
  }
}

/** The PATCH that turns the saved requirements into the edited ones. */
export function requirementsDiff(saved: Record<string, Json>, edited: Record<string, Json>): RequirementsDiff {
  const out: RequirementsDiff = { patch: {}, refusals: [] }
  diffInto('', saved, edited, out, out.patch)
  return out
}
