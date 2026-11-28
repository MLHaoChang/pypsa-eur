/**
 * expert-catalogue 05: the palette's ranking (`buildParameterIndex`) and the
 * server's `find_parameter` (`services/asset_schema/lookup.py`) answer the same
 * on one case table, read by both suites; so do `findEntry` and the server's
 * former-id resolver. The server filters by function and asset type before
 * ranking; the palette's lens only orders, so this suite filters the same way first.
 */
import { describe, expect, it } from 'vitest'
import { buildParameterIndex } from './searchIndex'
import { findEntry } from './openParameter'
import { entry } from './testEntries'
import { ASSET_TYPES } from '../utils/assetTypes'
import table from '../../../backend/tests/data/catalogue_search_cases.json'
import type { CatalogueEntry } from './api'

const ENTRIES: CatalogueEntry[] = table.entries.map(e => entry(e as unknown as Partial<CatalogueEntry> & Pick<CatalogueEntry, 'id' | 'plain_name'>))

interface SearchCase { query: string; ids: string[]; why: string; function?: string; asset_type?: string }

describe('parameter search parity with the server', () => {
  it.each((table.search as SearchCase[]).map(c => [c.why, c]))('%s', (_why, c) => {
    const classes = c.asset_type
      ? new Set(ASSET_TYPES.find(t => t.id === c.asset_type)?.match.map(m => m.cls) ?? [])
      : null
    const pool = ENTRIES.filter(e =>
      (!classes || classes.has(e.scope as never)) && (!c.function || c.function in e.functions))
    const hits = buildParameterIndex(pool).search(c.query, { lens: c.function ?? null, limit: 8 })
    expect(hits.map(h => h.entry.id)).toEqual(c.ids)
  })

  it.each(table.resolve.map(c => [c.id, c]))('resolves %s', (_id, c) => {
    expect(findEntry({ entries: ENTRIES }, c.id)?.id ?? null).toBe(c.current)
  })
})
