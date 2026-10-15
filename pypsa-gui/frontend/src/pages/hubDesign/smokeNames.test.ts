// The guided smoke (scripts/smoke-guided.mjs) mirrors displayName and
// riskName to check the rendered text (UX S6, G1): the mirror must agree.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { displayName } from '../../utils/displayName'
import { riskName } from './headline'

const SRC = readFileSync(resolve(__dirname, '../../../scripts/smoke-guided.mjs'), 'utf8')
const BLOCK = /const DN_ACRONYMS[\s\S]*?\n}\nfunction riskName[\s\S]*?\n}\n/.exec(SRC)?.[0] ?? ''
const mirror = new Function(`${BLOCK}; return { displayName, riskName }`)() as {
  displayName: (s: string) => string; riskName: (s: string) => string }

describe('the smoke mirrors the Guided names', () => {
  const IDS = ['grid_import', 'it_bus', 'site_transformer', 'genset_new_2030', 'ups_battery',
    'h2-tank', 'co2_store', 'poc_meter', 'Bus 0', 'Battery1', '', 'pv']
  it.each(IDS)('displayName(%j)', id => expect(mirror.displayName(id)).toBe(displayName(id)))
  it.each([...IDS, 'common_mode:grid_import,site_transformer'])('riskName(%j)',
    id => expect(mirror.riskName(id)).toBe(riskName(id)))
})
