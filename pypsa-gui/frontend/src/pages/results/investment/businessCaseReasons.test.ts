// Walkthrough 2026-11-17, issue 03 and 05: a KPI that is not established names the
// cause the engine holds (the served row reasons of the row it is computed from), in
// words; a refused run names the line and the column, never `lines[n].column`.
import { describe, expect, it } from 'vitest'
import type { BusinessCasePnl } from '../../../api/businessCase'
import { REFUSAL_WORDS } from './businessCaseModel'
import { firstFix, kpiText, REFUSAL_ACTION, refusedRunText, servedReasonText } from './businessCaseReport'

const item = (section: string | null, code: string) => ({ section, code })
const pnl = (row_reason: BusinessCasePnl['row_reason']): BusinessCasePnl =>
  ({ years: [2030], streams: {}, rows: {}, row_reason } as unknown as BusinessCasePnl)

// A code string anywhere after the "Not established:" lead-in.
const CODE = /_not_established|_missing|[a-z]_[a-z]|:/

const DC = pnl({
  project_post_tax: ['genset_1', 'genset_2', 'genset_new', 'rooftop_pv']
    .map(a => item('operating', `degradation_missing:${a}`)),
  ebitda: [item('operating', 'degradation_missing:genset_1')],
})
const IPP = pnl({
  project_post_tax: [item('terminal', 'terminal_value_missing'),
    item('terminal', 'terminal_part_unknown:Plant capex:investment')],
})
const TAX = pnl({
  project_post_tax: [item('tax', 'capex_not_established'), item('tax', 'input_missing:tax_losses'),
    item('tax', 'tax_pack_missing')],
})
const NE = { value: null, status: 'not_established', reason: 'operating_not_established' } as const

describe('a not-established KPI names the first blocker (walk 03)', () => {
  it('reads one blocker from the served reasons of the row it is computed from', () => {
    expect(kpiText(NE, 'amount', DC, 'project_npv')).toBe('Not established: needs a '
      + 'degradation rate for Genset 1, Genset 2, Genset new and Rooftop PV')
    expect(kpiText(NE, 'pct', DC, 'ebitda_margin_gross'))
      .toBe('Not established: needs a degradation rate for Genset 1')
    expect(kpiText(NE, 'amount', IPP, 'project_npv')).toBe('Not established: needs a terminal value')
    // A section that only waits on another yields to a cause.
    expect(kpiText(NE, 'amount', TAX, 'project_npv')).toBe('Not established: needs tax losses')
    for (const p of [DC, IPP, TAX]) {
      const t = kpiText(NE, 'amount', p, 'project_npv').replace(/^Not established: /, '')
      expect(t).not.toMatch(CODE)
    }
  })

  it('keeps a KPI-specific reason and the old words without served reasons', () => {
    const wacc = { value: null, status: 'not_established', reason: 'wacc_nominal_missing' } as const
    expect(kpiText(wacc, 'amount', DC, 'project_npv')).toBe('Not established: the WACC is not set')
    expect(kpiText(NE, 'amount', pnl(undefined), 'project_npv'))
      .toBe('Not established: the operating section is not established')
    expect(kpiText(NE, 'amount')).toBe('Not established: the operating section is not established')
  })

  it('merges one family over several assets, in display names', () => {
    expect(servedReasonText(DC.row_reason!.project_post_tax))
      .toBe('Not established: the operating section needs a degradation rate for Genset 1, '
        + 'Genset 2, Genset new and Rooftop PV.')
    expect(servedReasonText(IPP.row_reason!.project_post_tax)).toBe('Not established: the terminal '
      + 'value section needs a terminal value and a cost and lifetime for Plant capex (its investment part).')
  })

  it('says what to do first and where', () => {
    const kpis = { project_npv: NE, ebitda_margin_gross: { ...NE } }
    expect(firstFix(kpis, DC)).toEqual({
      action: 'Set a degradation rate for Genset 1, Genset 2, Genset new and Rooftop PV', place: 'finance' })
    expect(firstFix(kpis, TAX)?.action).toBe('Set tax losses')
    expect(firstFix(kpis, pnl({ project_post_tax: [item('operating', 'line_not_established:GoO')] })))
      .toEqual({ action: 'Fix the line “GoO”', place: 'lines' })
    expect(firstFix({ project_npv: { value: 1, status: 'ok', reason: null } }, DC)).toBeNull()
  })
})

describe('a refused run names the line and what to do (walk 05)', () => {
  const names = ['Capacity payment (BESS, firm capacity)', 'Guarantees of origin', 'Insurance']
  it('words the served refusal', () => {
    expect(refusedRunText('business_case_refused:indexation_missing: lines[1].indexation', names))
      .toBe('Guarantees of origin: set an indexation rate')
    expect(refusedRunText('business_case_refused:quantity_missing: lines[2].quantity', names))
      .toBe('Insurance: give a quantity, or fill every year')
    // A line past the saved list reads its number, never `lines[n]`.
    expect(refusedRunText('business_case_refused:quantity_missing: lines[7].quantity', names))
      .toBe('Line 8: give a quantity, or fill every year')
    // A kind with no action reads its cell words; text that is not a refusal is kept.
    expect(refusedRunText('business_case_refused:a_new_kind: lines[0].price', names))
      .toBe('Capacity payment (BESS, firm capacity): Price: refused')
    expect(refusedRunText('the run could not start', names)).toBe('the run could not start')
  })
  it('has an action only for kinds the cells know, each one sentence with no code', () => {
    for (const [k, a] of Object.entries(REFUSAL_ACTION)) {
      expect(k in REFUSAL_WORDS, k).toBe(true)
      expect(a, k).not.toMatch(/_|:/)
    }
  })
})
