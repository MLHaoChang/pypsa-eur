// Where the library, not the model, is the limit (visual-layers plan 3 S4):
// the objects the layout drew as their class's generic stand-in because no
// type in utils/assetTypes.ts names their carrier, grouped by class and
// carrier for one status line in the overlay. Pure, no three.
import type { SiteObject } from './layout'

export interface LibraryGap {
  cls: SiteObject['type']
  carrier: string
  /** The objects drawn generically, in site order. */
  names: string[]
}

/** The generic objects grouped by (class, carrier), in first-seen order. */
export function libraryGaps(objects: readonly Pick<SiteObject, 'type' | 'name' | 'carrier' | 'generic'>[]): LibraryGap[] {
  const out = new Map<string, LibraryGap>()
  for (const o of objects) {
    if (!o.generic) continue
    const key = `${o.type}\u0000${o.carrier}`
    const gap = out.get(key) ?? { cls: o.type, carrier: o.carrier, names: [] }
    gap.names.push(o.name)
    out.set(key, gap)
  }
  return [...out.values()]
}

const quoted = (carrier: string) => (carrier ? `'${carrier}'` : 'no carrier')

/** The status line, or null when every object has a specific type. */
export function libraryGapLine(gaps: readonly LibraryGap[]): string | null {
  if (gaps.length === 0) return null
  return gaps
    .map(g => `${quoted(g.carrier)} ${g.cls} drawn as ${g.names.length === 1 ? 'a generic block' : `generic blocks (${g.names.length})`}`)
    .join(' · ')
}
