// The verdict page (plan S8 acceptance; gate S6/S7 carries): three KPIs from
// a real findings payload with their basis labels; a null KPI reads "not
// established", never 0; with `available: false` the options never reached
// sit beside the class; `recommended_among_judged` and every disclosure code
// with its sentence; the NPV/IRR/payback by-construction note among them.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { Figure, Findings } from '../../api/decisionStudies'
import Verdict from './Verdict'
import { findings, findingsPvPre, ledger } from './__fixtures__/payloads'
import { HELP } from '../../utils/decisionVocabulary'

afterEach(() => cleanup())

function show(f: Findings) {
  return render(<Verdict findings={f} maturity={ledger.maturity} ledgerRows={ledger.ledger.rows} />)
}

describe('the verdict page', () => {
  it('shows the class, the sentence with its facts, and three KPIs with their basis', () => {
    show(findings)
    expect(screen.getByTestId('verdict-class').textContent).toBe('Recommended')
    const sentence = screen.getByTestId('verdict-sentence').textContent!
    expect(sentence).toContain('0.3 MW')
    expect(sentence).not.toContain('{{')
    const kpis = screen.getAllByTestId('verdict-kpi')
    expect(kpis).toHaveLength(3)
    expect(kpis[0].textContent).toContain('Battery NPV')
    expect(kpis[0].textContent).toContain('EUR 3.53 M')
    expect(kpis[0].textContent).toContain('real, pre-tax, no subsidy')
    expect(kpis[0].textContent).toContain('EUR of 2020')
    expect(kpis[0].textContent).toContain('against the grid-only baseline')
    expect(screen.getByTestId('verdict-maturity').textContent).toContain('Screening')
  })

  it('renders a null KPI as "not established", never 0', () => {
    const nul: Figure = { ...findings.verdict.headline_kpis[0], value: null, basis: null, currency_year: null, unavailable: 'currency_year_unknown' }
    show({ ...findings, verdict: { ...findings.verdict, headline_kpis: [nul, ...findings.verdict.headline_kpis.slice(1)], facts: { ...findings.verdict.facts, battery_npv: nul } } })
    const kpi = screen.getAllByTestId('verdict-kpi')[0]
    expect(kpi.textContent).toContain('not established')
    expect(kpi.textContent).not.toMatch(/EUR 0\b/)
    expect(within(kpi).queryByText(/^0$/)).toBeNull()
    expect(kpi.textContent).toContain(HELP.currency_year_unknown)
  })

  it('shows every disclosure with its sentence, the by-construction note among them', () => {
    show(findings)
    const list = screen.getByTestId('verdict-disclosures')
    for (const code of findings.verdict.disclosures) {
      expect(list.querySelector(`[data-code="${code}"]`)!.textContent).toContain(HELP[code])
    }
    expect(list.textContent).toContain('the sign restates its choice')
  })

  it('names the options never reached beside the class when the findings are not available', () => {
    show({ ...findings, available: false, options_status: 'not_established', pending_options: ['bess_2h', 'bess_4h'],
      verdict: { ...findings.verdict, status: 'not_established', class: null, reasons: ['options_not_established'] } })
    const head = screen.getByTestId('verdict-head')
    expect(within(head).getByTestId('verdict-class').textContent).toBe('Verdict not established')
    const pending = within(head).getByTestId('verdict-pending')
    expect(pending.textContent).toContain('Battery, 2 hours')
    expect(pending.textContent).toContain('Battery, 4 hours')
    expect(screen.getByTestId('verdict-reasons').textContent).toContain(HELP.options_not_established)
  })

  it('says a recommendation covers only the judged options', () => {
    show({ ...findings, verdict: { ...findings.verdict, sentence_template: 'recommended_among_judged',
      disclosures: [...findings.verdict.disclosures, 'options_not_all_judged'], reasons: ['options_not_all_judged'] } })
    expect(screen.getByTestId('verdict-among-judged').textContent).toContain('not every option was judged')
    expect(screen.getByTestId('verdict-disclosures').textContent).toContain(HELP.options_not_all_judged)
  })

  it('lists the reasons a verdict is not established, with their sentences', () => {
    show(findingsPvPre)
    expect(screen.getByTestId('verdict-class').textContent).toBe('Verdict not established')
    const reasons = screen.getByTestId('verdict-reasons')
    expect(reasons.textContent).toContain(HELP.options_not_all_judged)
    expect(reasons.textContent).toContain(HELP.bess_pv_value_not_attributable_to_battery)
  })

  it('names the top drivers by their ledger labels and the main caveat in words', () => {
    show(findings)
    const drivers = screen.getByTestId('verdict-drivers')
    expect(drivers.textContent).toContain('Discount rate (real, pre-tax)')
    expect(screen.getByTestId('verdict-caveat').textContent).toContain(HELP[findings.verdict.main_caveat!])
  })
})
