// The Expert island editor's save, once a sidecar exists: a PATCH of the
// requirements that changed, as plain values (control-strategy 02, GK B3 K4,
// K19). The server sets every source and refuses a client-sent one at any
// depth, so a stored `{value, source}` is never sent back: only the leaves
// that differ from the loaded config, and a source edited in the raw text is
// refused here, in words, before any request.

import { ISLAND_OTHER_REQUIREMENT_LABELS, ISLAND_REQUIREMENT_LABELS } from './islandWords'

export interface RequirementsDiff {
  /** The `requirements` PATCH; empty when nothing changed. */
  patch: Record<string, unknown>
  /** Edits the PATCH cannot carry, in words: the field's label first, or for a field
   *  with no label the words first and its key after (UX 30). */
  refusals: string[]
}

type Json = unknown

const LABELS: Record<string, string> = { ...ISLAND_REQUIREMENT_LABELS, ...ISLAND_OTHER_REQUIREMENT_LABELS }
const SOURCE_WORDS = 'the server sets sources. Enter the value only.'
const VALUE_WORDS = 'enter the value only.'
const REMOVE_WORDS = 'a setting cannot be removed here.'

/** "UPS bridge: the server sets sources. …", or "The server sets sources. … (a.b)" with no label. */
function refusal(where: string, words: string): string {
  const label = LABELS[where.split('.').pop() ?? '']?.replace(/ \([^)]*\)$/, '')
  return label ? `${label}: ${words}` : `${words[0].toUpperCase()}${words.slice(1)} (${where})`
}
const isObj = (v: Json): v is Record<string, Json> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isTagged = (v: Json): v is { value: Json; source: Json } =>
  isObj(v) && Object.keys(v).length === 2 && 'value' in v && 'source' in v
const same = (a: Json, b: Json) => JSON.stringify(a) === JSON.stringify(b)

function diffInto(path: string, saved: Record<string, Json>, edited: Record<string, Json>, out: RequirementsDiff,
  patch: Record<string, unknown>): void {
  for (const k of Object.keys(saved)) {
    if (!(k in edited)) out.refusals.push(refusal(`${path}${k}`, REMOVE_WORDS))
  }
  for (const [k, next] of Object.entries(edited)) {
    const where = `${path}${k}`
    const old = saved[k]
    if (isTagged(old)) {
      if (isTagged(next)) {
        if (!same(next.source, old.source)) out.refusals.push(refusal(where, SOURCE_WORDS))
        if (!same(next.value, old.value)) patch[k] = next.value
      } else if (isObj(next)) {
        out.refusals.push(refusal(where, VALUE_WORDS))
      } else if (!same(next, old.value)) {
        patch[k] = next
      }
    } else if (isObj(old) && isObj(next) && !isTagged(next)) {
      const sub: Record<string, unknown> = {}
      diffInto(`${where}.`, old, next, out, sub)
      if (Object.keys(sub).length) patch[k] = sub
    } else if (isTagged(next) || (isObj(next) && 'source' in next)) {
      out.refusals.push(refusal(where, SOURCE_WORDS))
    } else if (next === null) {
      out.refusals.push(refusal(where, REMOVE_WORDS))
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
