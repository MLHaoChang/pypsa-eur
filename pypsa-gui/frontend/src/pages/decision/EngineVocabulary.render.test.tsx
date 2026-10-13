// U2 WP8 part B (plan §5.4, gate C7): the engine vocabulary on the guided pages.
// The fixtures are route payloads in the U2 names (`tariff_engine` /
// `finance_engine`, `levelised_cost`, `terminal_value_eur`); a figure a study
// stored before U2 keeps the label of what made it (the earlier bill
// calculator or cash-flow model), said in plain words. The bill preview names
// the engine that priced it and explains every note on the bill.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Figure, Findings, IntakePreview } from '../../api/decisionStudies'
import Verdict from './Verdict'
import { BillPreview } from './TariffStep'
import ReportStep from './ReportStep'
import { rerunNeeded } from './decisionModel'
import { findings, ledger, optionCase, previewUpload, report } from './__fixtures__/payloads'
import { ENGINE_LABELS, ERROR_FALLBACK, HELP, errorCopy, helpFor } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

const BILL_NOTES = [
  'bill_resolution_differs_from_settlement', 'fixed_charge_prorated_on_partial_period',
  'demand_on_partial_month', 'peak_from_partial_year', 'capacity_charge_prorated_by_hours',
]

function previewWith(notes: string[]): IntakePreview {
  const ok = previewUpload.bill as Extract<IntakePreview['bill'], { status: 'ok' }>
  return { ...previewUpload, bill: { ...ok, bill: { ...ok.bill, honesty_notes: notes } } } as IntakePreview
}

describe('the U2 engine vocabulary', () => {
  it('the fixtures are payloads in the U2 names', () => {
    const dir = join(__dirname, '__fixtures__')
    for (const f of readdirSync(dir).filter(n => n.endsWith('.json'))) {
      const text = readFileSync(join(dir, f), 'utf8')
      expect([f, /bill_calculator|cash_flow_expander|"lcos"|"salvage_eur"/.test(text)]).toEqual([f, false])
    }
    expect(optionCase.engine).toBe('finance_engine')
    expect(optionCase.kpis!.levelised_cost).toBeGreaterThan(0)
    expect(optionCase.kpis!.levelised_cost_basis).toBe('real')
    expect(optionCase.kpis!.terminal_value_eur).toBeGreaterThan(0)
  })

  it('labels the engines in plain words, an earlier engine as earlier', () => {
    expect(ENGINE_LABELS.tariff_engine).toBe('tariff engine')
    expect(ENGINE_LABELS.finance_engine).toBe('finance engine')
    expect(ENGINE_LABELS.bill_calculator).toMatch(/^the earlier /)
    expect(ENGINE_LABELS.cash_flow_expander).toMatch(/^the earlier /)
  })

  it('reads a stored pre-U2 KPI against the baseline, like the engine’s', () => {
    const old: Figure = { ...findings.verdict.headline_kpis[0], engine: 'cash_flow_expander' }
    const f: Findings = { ...findings, verdict: { ...findings.verdict, headline_kpis: [old, ...findings.verdict.headline_kpis.slice(1)] } }
    render(<Verdict findings={f} maturity={ledger.maturity} ledgerRows={ledger.ledger.rows} />)
    expect(screen.getAllByTestId('verdict-kpi')[0].textContent).toContain('against the grid-only baseline')
  })

  it('names the engine that priced the bill preview', () => {
    render(<BillPreview preview={previewUpload} error={null} />)
    const text = screen.getByTestId('bill-preview').textContent!
    expect(text).toContain(ENGINE_LABELS.tariff_engine)
    expect(text).not.toContain('bill calculator')
  })

  it('explains every note on the previewed bill in the study’s own words', () => {
    render(<BillPreview preview={previewWith(BILL_NOTES)} error={null} />)
    const list = screen.getByTestId('bill-notes')
    for (const code of BILL_NOTES) {
      expect(HELP[code]).toBeTruthy()
      expect(list.querySelector(`[data-code="${code}"]`)!.textContent).toContain(HELP[code])
    }
  })

  it('shows no notes block for a bill without notes', () => {
    render(<BillPreview preview={previewWith([])} error={null} />)
    expect(screen.queryByTestId('bill-notes')).toBeNull()
  })
})

// Gate U2-WP8a (part A's binding conditions, taken in part B): Y1's refusals
// in plain words, and the report's re-run line for an earlier version's run.
describe('the part A gate conditions on the guided pages', () => {
  it('explains a finance the run could not compile in plain words (Y1)', () => {
    for (const code of ['currency_year_mixed', 'lifetime_not_whole_years', 'discount_rate_differs_from_lp']) {
      const copy = errorCopy(code)
      expect([code, copy]).not.toEqual([code, ERROR_FALLBACK])
      expect(`${copy.title} ${copy.action}`).not.toMatch(/_|engine|LP\b/)
    }
    expect(errorCopy('currency_year_mixed').title).toMatch(/currency year/)
    expect(errorCopy('currency_mixed').title).not.toMatch(/currency years/)
    expect(rerunNeeded('discount_rate_differs_from_lp')).toBe(true)
    expect(rerunNeeded('currency_year_mixed')).toBe(false)
  })

  it('a bar the engine did not solve says why (Y2)', () => {
    expect(HELP.variant_not_engine_solved).toBeTruthy()
    expect(helpFor('reference_variant_not_engine_solved').source).toBe('study')
  })

  it('asks for a re-run when the report was made from an earlier version’s run', () => {
    render(<ReportStep report={{ ...report, stale: true, stale_reasons: ['run_by_an_earlier_version'] }} error={null}
      assembleError={null} run={null} tornado={null} onAssemble={() => {}} busy={false} urls={{ html: '', docx: '', xlsx: '' }} />)
    expect(screen.getByTestId('report-rerun')).toBeTruthy()
  })
})
