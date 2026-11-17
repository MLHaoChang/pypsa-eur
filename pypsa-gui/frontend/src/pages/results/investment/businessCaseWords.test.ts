// Walk 2026-11-17 issue 06 (RAW-NAMES-1): every category, escalation class and P&L
// stream the backend can serve reads in words, never a capitalised id ("Sga", "Ppa").
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { hintCurrencyNote, ledgerKeysWords, ledgerKeyWords, outputLabelWords, CATEGORY_WORDS, categoryWords, ESCALATION_WORDS, escalationWords, STREAM_WORDS, streamWords } from './businessCaseWords'

const BACKEND = resolve(__dirname, '../../../../../backend')
const read = (p: string) => readFileSync(resolve(BACKEND, p), 'utf8')
const literal = (src: string, name: string) => {
  const m = new RegExp(`${name} = Literal\\[([^\\]]*)\\]`).exec(src)
  return [...(m?.[1] ?? '').matchAll(/"([a-z_]+)"/g)].map(x => x[1])
}
const dictKeys = (src: string, name: string) => {
  const i = src.indexOf(`${name}: dict`)
  return [...src.slice(i, src.indexOf('}', i)).matchAll(/"([a-z_]+)":/g)].map(x => x[1])
}
const MODEL = read('models/business_case.py')
const CASHFLOW = read('services/finance/cashflow.py')
const CATEGORIES = ['RevenueCategory', 'OpexCategory', 'CapexCategory'].flatMap(n => literal(MODEL, n))
const ESCALATIONS = literal(MODEL, 'Escalation')
const STREAMS = [...dictKeys(CASHFLOW, 'STREAM_CLASS'), ...dictKeys(CASHFLOW, 'BC_STREAM_CLASS'),
  ...literal(MODEL, 'OpexCategory').map(c => `bc_${c}`)]

describe('business-case ids in words (walk 06)', () => {
  it('reads the backend lists', () => {
    expect(CATEGORIES.length).toBeGreaterThan(20)
    expect(ESCALATIONS).toContain('contract')
    expect(STREAMS).toContain('energy_wholesale')
  })
  it('has words for every category and escalation class', () => {
    expect(Object.keys(CATEGORY_WORDS).sort()).toEqual([...new Set(CATEGORIES)].sort())
    expect(Object.keys(ESCALATION_WORDS).sort()).toEqual([...ESCALATIONS].sort())
    expect(categoryWords('sga')).toBe('Overheads (SG&A)')
    expect(escalationWords('ppa')).toBe('PPA price rate')
  })
  it('has words for every stream, never a raised id', () => {
    // Each has its own words (no fallback that raises the id: "Fom", "Ppa settlement").
    for (const s of STREAMS) {
      expect(s in STREAM_WORDS || s.slice(3) in CATEGORY_WORDS, s).toBe(true)
      expect(streamWords(s), s).not.toMatch(/_/)
    }
    expect(streamWords('energy_wholesale')).toBe('Wholesale market energy')
    expect(streamWords('ppa_settlement')).toBe('PPA settlement')
    expect(streamWords('bc_sga')).toBe('Overheads (SG&A)')
  })
})

describe('a public range in another currency (walk 04)', () => {
  it('says its currency, not converted, against the project currency', () => {
    expect(hintCurrencyNote('USD/kW_month', 'EUR')).toBe('In USD, not converted: type the price in EUR, the project currency.')
    expect(hintCurrencyNote('EUR/kg', 'eur')).toBeNull()
    expect(hintCurrencyNote('GBP/kW_year', null)).toBeNull()
    expect(hintCurrencyNote('kg', 'EUR')).toBeNull()
  })
})

describe('locked rows and model outputs in display names (walk 06, UX 2)', () => {
  it('words a ledger key by the item it names', () => {
    expect(ledgerKeyWords('bill:network_charges')).toBe('Network charges')
    expect(ledgerKeyWords('asset:fom:Generator:pv_plant')).toBe('PV plant')
    expect(ledgerKeyWords('contract:ppa:ppa_energy')).toBe('PPA energy')
    expect(ledgerKeyWords('bill_degradation:pv_plant')).toBe('Degradation, PV plant')
    // A name a person typed is kept.
    expect(ledgerKeyWords('contract:ppa:Wind PPA 2030')).toBe('Wind PPA 2030')
    // Two rows that would read alike read in full, never as twins.
    expect(ledgerKeysWords(['asset:export:Generator:pv_plant', 'bill:export:pv_plant', 'bill:network_charges']))
      .toEqual(['Asset · Export · PV plant', 'Bill · Export · PV plant', 'Network charges'])
  })
  it('words the asset at the end of an output label, and keeps other labels', () => {
    expect(outputLabelWords('Capacity in service: wind_farm')).toBe('Capacity in service: Wind farm')
    expect(outputLabelWords('Capacity in service: bess')).toBe('Capacity in service: BESS')
    expect(outputLabelWords('Served IT energy')).toBe('Served IT energy')
    expect(outputLabelWords('Energy delivered at the grid side of grid_export, after link losses'))
      .toBe('Energy delivered at the grid side of Grid export, after link losses')
    expect(outputLabelWords('Capacity in service: Rooftop PV')).toBe('Capacity in service: Rooftop PV')
  })
})
