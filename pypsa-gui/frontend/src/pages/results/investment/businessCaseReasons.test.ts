// Walkthrough 2026-11-17, issue 03 and 05: a KPI that is not established names the
// cause the engine holds (the served row reasons of the row it is computed from), in
// words; a refused run names the line and the column, never `lines[n].column`.
import { describe, expect, it } from 'vitest'
import type { BusinessCasePnl } from '../../../api/businessCase'
import { firstFix, kpiText, refusedRunText, servedReasonText } from './businessCaseReport'

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
  it('reads the served reasons of the row it is computed from', () => {
    expect(kpiText(NE, 'amount', DC, 'project_npv')).toBe('Not established: the operating section needs a '
      + 'degradation rate for genset 1, genset 2, genset new and rooftop pv.')
    expect(kpiText({ ...NE, reason: 'operating_not_established' }, 'pct', DC, 'ebitda_margin_gross'))
      .toBe('Not established: the operating section needs a degradation rate for genset 1.')
    expect(kpiText(NE, 'amount', IPP, 'project_npv')).toBe('Not established: the terminal value section needs a '
      + 'terminal value and a cost and lifetime for Plant capex (its investment part).')
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

  it('merges one family over several assets and never repeats a phrase', () => {
    expect(servedReasonText(DC.row_reason!.project_post_tax))
      .toBe('Not established: the operating section needs a degradation rate for genset 1, '
        + 'genset 2, genset new and rooftop pv.')
  })

  it('says what is needed first and where, skipping a section that only waits on another', () => {
    const kpis = { project_npv: NE, ebitda_margin_gross: { ...NE } }
    expect(firstFix(kpis, DC)).toEqual({
      text: 'Needed first: a degradation rate for genset 1, genset 2, genset new and rooftop pv, in the finance inputs.',
      place: 'finance',
    })
    expect(firstFix(kpis, TAX)?.text).toBe('Needed first: tax losses, in the finance inputs.')
    expect(firstFix(kpis, pnl({ project_post_tax: [item('operating', 'line_not_established:GoO')] })))
      .toEqual({ text: 'Needed first: the line “GoO”, in the lines above.', place: 'lines' })
    expect(firstFix({ project_npv: { value: 1, status: 'ok', reason: null } }, DC)).toBeNull()
  })
})

describe('a refused run names the line and the column (walk 05)', () => {
  const names = ['Capacity payment (BESS, firm capacity)', 'Guarantees of origin', 'Insurance']
  it('words the served refusal', () => {
    expect(refusedRunText('business_case_refused:indexation_missing: lines[1].indexation', names))
      .toBe('Guarantees of origin › Indexation: needed with contract escalation')
    expect(refusedRunText('business_case_refused:quantity_missing: lines[2].quantity', names))
      .toBe('Insurance › Quantity: give a quantity or fill every year')
    // A line past the saved list reads its number, never `lines[n]`.
    expect(refusedRunText('business_case_refused:quantity_missing: lines[7].quantity', names))
      .toBe('Line 8 › Quantity: give a quantity or fill every year')
    // Not a refusal string: kept as served.
    expect(refusedRunText('the run could not start', names)).toBe('the run could not start')
    for (const t of [refusedRunText('business_case_refused:indexation_missing: lines[0].indexation', names)]) {
      expect(t).not.toMatch(/lines\[|business_case_refused|_missing/)
    }
  })
})
