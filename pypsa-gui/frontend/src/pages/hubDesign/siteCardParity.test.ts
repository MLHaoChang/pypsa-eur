// The Site / Goal card's study request, pinned to the backend's copy
// (project-workflow B1, gatekeeper point 3). `flow_service.site_card_study_body`
// re-derives `buildEhStudyBody(archetype, formFromTemplate(meta))` in Python
// for the project flow; both sides are checked against the same rows in
// `backend/tests/fixtures/flow_site_card_args.json` (the pytest pins the rows'
// metadata to every shipped template). Change one side and this, or
// `test_flow_site_card_parity.py`, fails.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildEhStudyBody, formFromTemplate } from '../results/EhReferenceDesignPanel'
import type { EhArchetype, EhTemplateMeta } from '../../api/simulation'

type Row = { id: string, meta: Record<string, unknown>, study_body: Record<string, unknown> | null }

const rows: Row[] = JSON.parse(readFileSync(
  resolve(process.cwd(), '..', 'backend', 'tests', 'fixtures', 'flow_site_card_args.json'), 'utf8'))

describe('Site card request parity with the project flow', () => {
  it('has the shipped templates and the synthetic rules', () => {
    const ids = rows.map(r => r.id)
    expect(ids).toContain('eh_datacenter')
    expect(ids).toContain('synthetic:all_rules')
    expect(ids).toContain('synthetic:out_of_range_is_null')
  })

  it.each(rows.map(r => [r.id, r] as const))('%s', (_id, row) => {
    const meta = row.meta as unknown as EhTemplateMeta
    const built = buildEhStudyBody(meta.recommended_archetype as EhArchetype, formFromTemplate(meta))
    expect(built.body).toEqual(row.study_body)
  })
})
