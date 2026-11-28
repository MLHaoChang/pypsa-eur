/**
 * expert-catalogue 05: the server's asset-type matcher (`find_parameter`'s
 * `asset_type` filter, `services/asset_schema/lookup.py`) and `matchType` answer
 * the same on one case table, read by both suites. The server has no site
 * members, so a rule's `port` is the one place they may differ; each such case
 * names it.
 */
import { describe, expect, it } from 'vitest'
import { matchType } from './assetTypes'
import table from '../../../backend/tests/data/asset_type_match_cases.json'

interface Case {
  why: string
  cls: string
  row: Record<string, unknown> & { name: string }
  buses: Record<string, string>
  members?: string[]
  frontend: string | null
  backend: string | null
}

describe('asset-type matcher parity with the server', () => {
  it.each((table.cases as Case[]).map(c => [c.why, c]))('%s', (_why, c) => {
    const ctx = { members: c.members ?? Object.keys(c.buses), busCarrier: (b: string) => c.buses[b] }
    expect(matchType(c.cls, c.row, ctx)?.type.id ?? null).toBe(c.frontend)
    if (c.frontend !== c.backend) expect(c.why).toMatch(/port/)
  })
})
