// Template-e2e issue 22, S2b: the Investment figure's reason (Guided's Results
// headline and its tile) names a refusal's need in plain words; never a raw code.
import { describe, expect, it } from 'vitest'
import { chapterTiles, type ChapterInputs } from './chapters'

const NONE: ChapterInputs = { investment: { study: null, report: null }, grid: { campus: null, notHub: true },
  reliability: { hasResults: false, running: false, lole: null, target: null, verdict: null }, operation: { status: null } }
const tile = (study: unknown) =>
  chapterTiles({ ...NONE, investment: { study: study as never, report: null } }).find(t => t.id === 'investment')!

describe("the Investment figure's refusal", () => {
  it('a refusal with a plain need names it; never the code or its message', () => {
    expect(tile({ status: 'refused', error_code: 'cod_missing', error: 'cod_missing: bess has no cod' }).reason)
      .toBe('The investment case needs: a start date for each piece of new equipment.')
  })

  it('a refusal with only a code: no raw code, Guided points to Expert', () => {
    expect(tile({ status: 'error', error_code: 'internal_error' }).reason)
      .toBe('The investment case could not finish. Open it in Expert for the details.')
  })

  it('a refusal with its own reason keeps the reason, without the code prefix', () => {
    expect(tile({ status: 'refused', error_code: 'owner_asset_missing',
                  error: 'owner_asset_missing: no owner assets' }).reason)
      .toBe('The investment case could not finish: no owner assets.')
  })
})
