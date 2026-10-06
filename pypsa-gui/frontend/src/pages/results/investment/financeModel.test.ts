// The finance form and report mappings (IC P4 WP4.7a/b), without a DOM.
import { describe, expect, it } from 'vitest'
import {
  basisStatement, cashflowPivot, cashTotals, counterfactualStatement, errorKey, errorsFor, established, financeErrors, fmtCell,
  fmtHeadline, headlines, numOrNull, parseRateOrList, perYearLists, progressFraction, progressText,
  rateOrListText, studyFailure, studyProgress, studyStale, toTable, waccGate, withKey,
} from './financeModel'
import type { CashflowLine, InvestmentCaseReportPayload } from '../../../api/types'

describe('the form mappings', () => {
  it('an empty number is null, never 0', () => {
    expect(numOrNull('')).toBeNull()
    expect(numOrNull('  ')).toBeNull()
    expect(numOrNull('0')).toBe(0)
    expect(numOrNull('2.5')).toBe(2.5)
  })

  it('withKey sets or removes a key without touching the rest', () => {
    const o = { a: 1, b: null }
    expect(withKey(o, 'a', undefined)).toEqual({ b: null })
    expect(withKey(o, 'c', 0)).toEqual({ a: 1, b: null, c: 0 })
    expect(o).toEqual({ a: 1, b: null })
  })

  it('a rate is one number or a list; a one-element list stays a list', () => {
    expect(parseRateOrList('0.05')).toBe(0.05)
    expect(parseRateOrList('0.05, 0.055')).toEqual([0.05, 0.055])
    expect(parseRateOrList('0.05,')).toEqual([0.05])
    expect(parseRateOrList('')).toBeUndefined()
    expect(parseRateOrList('five')).toBeNull()
    expect(rateOrListText([0.05])).toBe('0.05,')
    expect(parseRateOrList(rateOrListText([0.05]))).toEqual([0.05])
    expect(rateOrListText([0.05, 0.06])).toBe('0.05, 0.06')
    expect(rateOrListText(null)).toBe('')
  })
})

describe('the 422 mapping', () => {
  it('strips the body / finance prefix', () => {
    expect(errorKey(['body', 'finance', 'debt', 0, 'rate'])).toBe('debt.0.rate')
    expect(errorKey(['debt', 0, 'rate'])).toBe('debt.0.rate')
    expect(errorKey(['body', 'finance'])).toBe('')
  })

  it('reads a FastAPI list, the route’s errors list and a plain refusal', () => {
    expect(financeErrors([{ loc: ['body', 'finance', 'wacc_nominal'], msg: 'bad' }]))
      .toEqual({ wacc_nominal: ['bad'] })
    expect(financeErrors({ code: 'finance_inputs_invalid', message: 'x',
                           errors: [{ loc: ['debt', 1, 'tenor_years'], msg: '≥ 1' }] }))
      .toEqual({ 'debt.1.tenor_years': ['≥ 1'] })
    expect(financeErrors({ code: 'participants_mismatch', message: 'differs' })).toEqual({ '': ['differs'] })
    expect(financeErrors('nope')).toEqual({ '': ['nope'] })
  })

  it('places an error at its field, and leftovers at the section or the top', () => {
    const errs = { 'debt.0.rate.float': ['not a number'], 'debt.0': ['sizing'], '': ['sum to 1'],
                   'wacc_nominal': ['≥ 0'] }
    expect(errorsFor(errs, 'debt.0.rate')).toEqual(['float: not a number'])
    expect(errorsFor(errs, 'debt.0', ['debt.0.rate'])).toEqual(['sizing'])
    expect(errorsFor(errs, '', ['debt', 'wacc_nominal'])).toEqual(['sum to 1'])
    expect(errorsFor(errs, 'wacc_nominal')).toEqual(['≥ 0'])
  })
})

describe('the report', () => {
  it('reads a not-established value as null, never 0', () => {
    expect(established(null)).toBeNull()
    expect(established(undefined)).toBeNull()
    expect(established('not_established')).toBeNull()
    expect(established({ value: null, reason: 'x' })).toBeNull()
    expect(established(Number.NaN)).toBeNull()
    expect(established(0)).toBe(0)
    expect(fmtHeadline('pct', null)).toBe('not established')
    expect(fmtHeadline('pct', 0)).toBe('0.00 %')
    expect(fmtCell('dscr', null)).toBe('not established')
    expect(fmtCell('year', 2031)).toBe('2031')
    expect(fmtCell('dscr', 1.4567)).toBe('1.46')
  })

  it('the report headline wins over the section; a null headline stays null', () => {
    const rows = headlines({ project_irr_pre_tax: null, sections: { project: { status: 'ok',
      payload: { project_pre_tax_irr: 0.2, equity_post_tax_irr: 0.1 } } } })
    expect(rows.find(h => h.id === 'project_irr_pre_tax')!.value).toBeNull()
    expect(rows.find(h => h.id === 'equity_irr_post_tax')!.value).toBe(0.1)
    // lcoe_real is shown only when the report carries it.
    expect(rows.some(h => h.id === 'lcoe_real')).toBe(false)
  })

  it('the storage LCOS shows only with storage, a null one with its reason', () => {
    const none = headlines({ sections: { project: { status: 'ok', payload: {
      lcos_nominal_per_mwh: null, lcos: { assets: {}, reasons: ['no_storage'] } } } } })
    expect(none.some(h => h.id.startsWith('lcos'))).toBe(false)
    const rows = headlines({ sections: { project: { status: 'ok', payload: {
      lcos_nominal_per_mwh: 136.39, lcos_real_per_mwh: null,
      lcos: { assets: { bess: {} }, reasons: ['wacc_nominal_missing'] } } } } })
    expect(rows.find(h => h.id === 'lcos')!.value).toBe(136.39)
    const real = rows.find(h => h.id === 'lcos_real')!
    expect(real.value).toBeNull()
    expect(real.note).toBe('wacc_nominal_missing')
    const noInfl = headlines({ sections: { project: { status: 'ok', payload: {
      lcos_nominal_per_mwh: null, lcos_real_per_mwh: null,
      lcos: { assets: { bess: {} }, reasons: ['no_discharge:bess', 'real_not_established:inflation_missing'] } } } } })
    expect(noInfl.find(h => h.id === 'lcos')!.note).toBe('no_discharge:bess')
    expect(noInfl.find(h => h.id === 'lcos_real')!.note)
      .toBe('no_discharge:bess; real_not_established:inflation_missing')
  })

  it('states the price basis in words, or null', () => {
    expect(basisStatement({ sections: { project: { status: 'ok', payload: {
      basis_statement: 'real basis, 2020 EUR' } } } })).toBe('real basis, 2020 EUR')
    expect(basisStatement({})).toBeNull()
  })

  it('a headline of a not-established section carries its reason', () => {
    const rows = headlines({ sections: { project: { status: 'not_established', note: 'refused:no_owner' } } })
    expect(rows.find(h => h.id === 'equity_irr_post_tax')!.note).toBe('refused:no_owner')
  })

  it('the WACC gate: true, false, and null / absent are not established', () => {
    expect(waccGate({ gates: { wacc_vs_discount_rate_consistent: true } })).toBe('consistent')
    expect(waccGate({ wacc_vs_discount_rate_consistent: false })).toBe('differs')
    expect(waccGate({ wacc_vs_discount_rate_consistent: null })).toBe('not_established')
    expect(waccGate({})).toBe('not_established')
  })

  it('states the counterfactual from the report', () => {
    expect(counterfactualStatement({ sections: { project: { status: 'ok',
      payload: { counterfactual_statement: 'against the site without PV' } } } }))
      .toBe('against the site without PV')
    expect(counterfactualStatement({ sections: { project: { status: 'ok',
      payload: { has_counterfactual: false } } } })).toMatch(/no counterfactual/)
    expect(counterfactualStatement({})).toBeNull()
  })

  it('pivots the cashflow lines by year and stream', () => {
    const line = (year: number, stream: string, amount: number, participant = 'owner') => ({
      year, participant, counterparty: 'x', value_stream: stream, amount,
      provenance: { source: 's', mode: 'pf' } }) as CashflowLine
    const p = cashflowPivot([line(2031, 'energy', 10), line(2030, 'capex', -5), line(2031, 'energy', 2)])
    expect(p.years).toEqual([2030, 2031])
    expect(p.columns).toEqual(['energy', 'capex'])
    expect(p.cells[2031]).toEqual({ energy: 12 })
    expect(p.totals).toEqual({ 2030: -5, 2031: 12 })
    const two = cashflowPivot([line(2030, 'energy', 1), line(2030, 'energy', 1, 'host')])
    expect(two.columns).toEqual(['owner · energy', 'host · energy'])
    expect(cashflowPivot(undefined).years).toEqual([])
    const cfLine = { ...line(2030, 'energy_import', 100),
                     provenance: { source: 'counterfactual:bill', mode: 'pf' } } as CashflowLine
    expect(cashflowPivot([line(2030, 'energy_import', -60), cfLine]).columns)
      .toEqual(['energy_import', 'avoided vs counterfactual · energy_import'])
  })

  it('marks the cells of a not-established line, never "no line" or a partial sum (P4 gate B2)', () => {
    const line = (year: number, stream: string, amount: number) => ({ year, participant: 'owner',
      counterparty: 'x', value_stream: stream, amount, provenance: { source: 'ledger', mode: 'pf' } }) as CashflowLine
    const p = cashflowPivot([line(2031, 'energy_import', -60), line(2031, 'ppa_settlement', 200)],
      [{ value_stream: 'energy_import', source: 'bill', years: [2031, 2032] },
       { value_stream: 'energy_import', source: 'counterfactual:bill', years: [2031] }])
    expect(p.unknown[2031]).toEqual(['energy_import', 'avoided vs counterfactual · energy_import'])
    expect(p.unknown[2032]).toEqual(['energy_import'])
    expect(p.years).toEqual([2031, 2032])           // a year with only an unknown line has a row
    expect(p.totals[2031]).toBe(140)                 // the pivot's sum is NOT the total (cashTotals)
    expect(cashflowPivot([line(2031, 'fom', -1)]).unknown).toEqual({})
  })

  it('takes the totals from the equity cash, never from a partial sum of lines', () => {
    const line = (year: number, amount: number) => ({ year, participant: 'owner', counterparty: 'x',
      value_stream: 'ppa_settlement', amount, provenance: { source: 's', mode: 'pf' } }) as CashflowLine
    const pivot = cashflowPivot([line(2030, -10), line(2031, 5)])
    const report = (cash: unknown, tax = 'ok') => ({ completeness: { project: 'ok', tax },
      sections: { project: { status: 'ok', payload: { years: [2030, 2031], cash } },
                  tax: { status: tax, note: tax === 'ok' ? null : 'tax_pack_missing' } } }) as
      unknown as InvestmentCaseReportPayload
    expect(cashTotals(report({ equity_post_tax: [-10, 5] }), pivot))
      .toEqual({ totals: { 2030: -10, 2031: 5 }, reason: null, mismatch: [] })
    const none = cashTotals(report({ equity_post_tax: null }, 'not_established'), pivot)
    expect(none.totals).toEqual({ 2030: null, 2031: null })
    expect(none.reason).toBe('the tax section: tax_pack_missing')
    expect(cashTotals(report({ equity_post_tax: [-10, 6] }), pivot).mismatch).toEqual([2031])
    expect(cashTotals(report({ equity_post_tax: [-10] }), pivot).totals[2031]).toBeNull()
  })

  it('tables a list of records, and per-year lists by the report’s years', () => {
    expect(toTable([{ dscr: 1.2, year: 2031 }])).toEqual({ columns: ['year', 'dscr'],
                                                          rows: [{ dscr: 1.2, year: 2031 }] })
    expect(toTable({ dscr: [null, 1.5], service: [null, 2] }, [2030, 2031])).toEqual({
      columns: ['year', 'dscr', 'service'],
      rows: [{ year: 2030, dscr: null, service: null }, { year: 2031, dscr: 1.5, service: 2 }] })
    expect(toTable({ years: [2030], x: [1] })!.columns).toEqual(['year', 'x'])
    expect(toTable({ dscr: [1, 2] })!.columns).toEqual(['#', 'dscr'])
    expect(toTable({ a: 1 })).toBeNull()
    // Only the lists of the axis length are per-year (a rate list is not).
    expect(Object.keys(perYearLists({ dscr: [1, 2, 3], rate: [0.05], flags: ['a', 'b', 'c'] }, [1, 2, 3])))
      .toEqual(['dscr'])
  })
})

describe('the study record', () => {
  it('reads the progress from a fraction, a record or the runner’s stages', () => {
    expect(progressFraction(0.4)).toBe(0.4)
    expect(progressFraction(40)).toBe(0.4)
    expect(progressFraction({ done: 1, total: 4 })).toBe(0.25)
    expect(progressFraction(null)).toBeNull()
    expect(progressText(studyProgress({ status: 'running', stage: 'load_pack', stages: ['a', 'b', 'c', 'd'],
                                        stages_done: ['a'] }))).toBe('load pack · 1 of 4 · 25 %')
    expect(progressText(studyProgress({ status: 'running' }))).toBeNull()
  })

  it('stale from the record or the report block', () => {
    expect(studyStale({ status: 'done', stale: true })).toEqual({ stale: true, changed: [], reason: null })
    expect(studyStale({ status: 'done', report: { stale: true, changed: ['finance'] } }))
      .toEqual({ stale: true, changed: ['finance'], reason: null })
    expect(studyStale({ status: 'done', report: { stale: false } }).stale).toBe(false)
    expect(studyStale(null).stale).toBe(false)
  })

  it('names the failure of an error, failed or refused run', () => {
    expect(studyFailure({ status: 'done' })).toBeNull()
    expect(studyFailure({ status: 'error', code: 'x', message: 'boom' })).toBe('The run failed: boom (x)')
    expect(studyFailure({ status: 'failed', error: 'kaput' })).toBe('The run failed: kaput')
    expect(studyFailure({ status: 'refused', error_code: 'cod_mismatch' }))
      .toBe('The case was refused: cod_mismatch')
  })
})

describe('WP4.7 review B3/B4', () => {
  it('names why there is no counterfactual and why staleness is unconfirmed', async () => {
    const m = await import('./financeModel')
    const s = m.counterfactualStatement({ sections: { project: { status: 'ok',
      payload: { has_counterfactual: false } } } } as never)
    expect(s).toContain('not the site party that pays the bill')
    const st = m.studyStale({ status: 'done', report: { present: true, stale: true, changed: [],
      reason: 'solve_in_flight' } } as never)
    expect(st.reason).toBe('solve_in_flight')
    expect(m.uncheckableReason(st.reason)).toContain('a solve is running')
    expect(m.uncheckableReason(null)).toBeNull()
  })
})
