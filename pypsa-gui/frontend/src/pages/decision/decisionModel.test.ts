// Pure predicates of the guided flow (plan S8 "decisionModel.ts: pure
// predicates … unit-tested without rendering"). Fixtures are payloads the
// backend actually produced (the S4 fake solver through the real routes).
import { describe, expect, it } from 'vitest'
import type { DecisionReport, Figure, Findings, LedgerRow, RunRecord } from '../../api/decisionStudies'
import {
  entryState, expertTarget, figureText, isPvRowUnused, ledgerCurrencyYearLabel, missingInputs,
  parseNeedsAttention, pollInterval, renderSentence, reportNeedsReassembly, rerunNeeded,
  sectionStatuses, sortTornado, streamsBasis, verdictTone, maturityLabel, isMoneyUnit,
} from './decisionModel'
import { findings, findingsPv, ledger, report, run, study } from './__fixtures__/payloads'

const nullKpi: Figure = {
  key: 'battery_npv', label: 'Battery NPV', value: null, unit: 'EUR', basis: null, currency_year: null,
  engine: 'cash_flow_expander', fidelity: 'quick_screen', unavailable: 'currency_year_unknown',
}

describe('the intake and the entry state', () => {
  it('names the mandatory inputs the pack refuses without (packs.missing_inputs)', () => {
    expect(missingInputs({})).toEqual(['site', 'connection_limit', 'load'])
    expect(missingInputs(study.intake)).toEqual([])
    expect(missingInputs({ site: { zone: ' ', connection_mw: 0 }, load: {} })).toEqual(['site', 'connection_limit', 'load'])
  })

  it('opens the intake on an incomplete pack study and the hub on a complete one', () => {
    expect(entryState(study)).toBe('hub')
    expect(entryState({ ...study, intake: { site: { zone: 'DE' } } })).toBe('intake')
    // A record attached to an existing project runs no pack: it has no intake to finish.
    expect(entryState({ ...study, pack_project: null, intake: {} })).toBe('hub')
  })
})

describe('the ledger', () => {
  const pvRow = (key: string) => ({ ...ledger.ledger.rows[0], key }) as LedgerRow

  it('marks PV rows unused when PV is off, and the other PV kind unused when it is on', () => {
    expect(isPvRowUnused(pvRow('pv_rooftop_eur_per_kw'), { pv: { enabled: false } })).toBe(true)
    expect(isPvRowUnused(pvRow('pv_rooftop_eur_per_kw'), {})).toBe(true)
    expect(isPvRowUnused(pvRow('pv_rooftop_eur_per_kw'), { pv: { enabled: true } })).toBe(false)
    expect(isPvRowUnused(pvRow('pv_utility_eur_per_kw'), { pv: { enabled: true } })).toBe(true)
    expect(isPvRowUnused(pvRow('pv_utility_eur_per_kw'), { pv: { enabled: true, kind: 'utility' } })).toBe(false)
    expect(isPvRowUnused(pvRow('discount_rate'), { pv: { enabled: false } })).toBe(false)
  })

  it('parses the needs_attention:<key>:<reason> protocol, reason included', () => {
    expect(parseNeedsAttention([
      'technology_costs_are_2030_projections_in_2020_eur',
      'needs_attention:demand_charge_price:not_applicable',
      'needs_attention:energy_price_level:unit_changed_EUR/MWh_to_multiplier',
    ])).toEqual([
      { key: 'demand_charge_price', reason: 'not_applicable' },
      { key: 'energy_price_level', reason: 'unit_changed_EUR/MWh_to_multiplier' },
    ])
  })

  it('states the currency year beside a money row, and only a money row', () => {
    const rows = Object.fromEntries(ledger.ledger.rows.map(r => [r.key, r]))
    expect(ledgerCurrencyYearLabel(rows.battery_storage_eur_per_kwh)).toBe('EUR of 2020')
    expect(ledgerCurrencyYearLabel(rows.demand_charge_price)).toBe('EUR of 2020')
    expect(ledgerCurrencyYearLabel(rows.battery_inverter_lifetime_years)).toBeNull()
    expect(ledgerCurrencyYearLabel({ ...rows.battery_storage_eur_per_kwh, currency_year: null })).toBe('EUR, currency year not stated')
    expect(isMoneyUnit('EUR/MW/month')).toBe(true)
    expect(isMoneyUnit('%/year')).toBe(false)
  })

  it('labels maturity from ONE source, the ledger payload', () => {
    expect(maturityLabel(ledger.maturity)).toBe('Screening')
    expect(maturityLabel({ ...ledger.maturity, status: 'not_established', class: null })).toBe('Maturity not established')
  })
})

describe('figures: null is "not established", never 0', () => {
  it('renders a null KPI as not established', () => {
    expect(figureText(nullKpi)).toBe('not established')
  })

  it('renders a real zero as a zero, with its unit', () => {
    expect(figureText({ ...nullKpi, value: 0, unavailable: null, currency_year: 2020 })).toBe('EUR 0')
  })

  it('renders a near-zero value as the value, not as 0', () => {
    expect(figureText({ ...nullKpi, key: 'p', unit: 'MW', value: 0.0004, unavailable: null, engine: 'lp' })).toBe('0.0004 MW')
  })

  it('formats money, years, MW and hours', () => {
    const kpis = findings.verdict.headline_kpis
    expect(figureText(kpis[0])).toBe('EUR 3.53 M')
    expect(figureText(kpis[1])).toBe('0.3 MW')
    expect(figureText(kpis[2])).toBe('0.4 years')
  })

  it('substitutes fact references in the verdict sentence, a null fact as not established', () => {
    const v = findings.verdict
    const text = renderSentence(v.sentence!, v.facts)
    expect(text).toContain('0.3 MW')
    expect(text).toContain('EUR 3.53 M')
    expect(text).not.toMatch(/\{\{/)
    expect(renderSentence('NPV {{battery_npv}} and {{missing}}', { battery_npv: nullKpi }))
      .toBe('NPV not established and not established')
  })
})

describe('the verdict and the findings', () => {
  it('tones the verdict by its class, and neutral when not established', () => {
    expect(verdictTone(findings.verdict, true)).toBe('good')
    expect(verdictTone({ ...findings.verdict, class: 'marginal' }, true)).toBe('caution')
    expect(verdictTone({ ...findings.verdict, class: 'not_recommended' }, true)).toBe('bad')
    expect(verdictTone({ ...findings.verdict, status: 'not_established', class: null }, true)).toBe('unknown')
    expect(verdictTone(findings.verdict, false)).toBe('unknown')
  })

  it('labels the waterfall by value_streams_basis', () => {
    expect(streamsBasis(findings)).toBe('baseline')
    expect(streamsBasis(findingsPv)).toBe('pv_only_reference')
    expect(streamsBasis({ ...findings, value_streams_status: 'not_established', value_streams_basis: null })).toBeNull()
  })

  it('sorts tornado bars by swing, largest first, unknown swings last', () => {
    const rows = [...findings.robustness.tornado].reverse()
    rows.push({ ...rows[0], key: 'x', swing: null, npv_low: null, npv_high: null })
    const sorted = sortTornado(rows).map(r => r.swing)
    expect(sorted.slice(0, -1)).toEqual([...sorted.slice(0, -1)].sort((a, b) => (b ?? 0) - (a ?? 0)))
    expect(sorted.at(-1)).toBeNull()
  })

  it('points the Expert view at the chosen option fork, never at the base project', () => {
    const t = expertTarget(findings, study)!
    const named = findings.options.find(o => o.option_id === findings.verdict.option_id)!
    expect(t).toEqual({ optionId: named.option_id, projectRef: named.project_ref })
    expect(t.projectRef).not.toBe(study.base_project)
    expect(expertTarget(findings, study, 'bess_4h')!.optionId).toBe('bess_4h')
    // A fork reference that IS the base (a copied or corrupt record) is refused.
    const corrupt = { ...findings, options: findings.options.map(o => ({ ...o, project_ref: study.base_project })) }
    expect(expertTarget(corrupt, study)).toBeNull()
    // No named option and no choice: no target.
    expect(expertTarget({ ...findings, verdict: { ...findings.verdict, option_id: null } }, study)).toBeNull()
  })
})

describe('polling: the status routes only, while running', () => {
  it('polls a running record and stops on a terminal one', () => {
    expect(pollInterval(run as RunRecord, 2000)).toBe(false)
    expect(pollInterval({ ...(run as RunRecord), status: 'running' }, 2000)).toBe(2000)
    expect(pollInterval(undefined, 2000)).toBe(false)
  })
})

describe('the report', () => {
  it('offers re-assembly when the run or the tornado finished after the report was generated', () => {
    const gen = Date.parse(report.generated_at) / 1000
    expect(reportNeedsReassembly(report, { finished_at: gen - 10 }, { finished_at: gen - 5 })).toBe(false)
    expect(reportNeedsReassembly(report, { finished_at: gen - 10 }, { finished_at: gen + 60 })).toBe(true)
    expect(reportNeedsReassembly(report, { finished_at: gen + 1 }, null)).toBe(true)
    expect(reportNeedsReassembly(null as unknown as DecisionReport, null, null)).toBe(false)
  })

  it('knows which refusals mean the study must be run again', () => {
    for (const code of ['intake_changed_since_run', 'ledger_changed_since_run', 'fork_changed_since_run', 'baseline_not_solved']) {
      expect(rerunNeeded(code)).toBe(true)
    }
    expect(rerunNeeded('study_running')).toBe(false)
    expect(rerunNeeded(null)).toBe(false)
  })
})

describe('the hub section chips', () => {
  it('reads every section off the study, the ledger, the run, the findings and the report', () => {
    const s = sectionStatuses({ study, ledger, run: run as RunRecord, findings: { data: findings as Findings }, report: { data: report } })
    expect(s.site).toBe('done')
    expect(s.demand).toBe('using_defaults')           // a synthetic sector profile
    expect(s.assumptions).toBe('using_defaults')      // key drivers still at library defaults
    expect(s.run).toBe('done')
    expect(s.findings).toBe('done')
    expect(s.report).toBe('done')
  })

  it('says a re-run is needed when the findings refuse with a changed intake', () => {
    const s = sectionStatuses({ study, ledger, run: run as RunRecord, findings: { errorCode: 'intake_changed_since_run' }, report: { data: { ...report, stale: true } } })
    expect(s.findings).toBe('rerun_needed')
    expect(s.report).toBe('stale')
  })

  it('flags an assumption that needs attention', () => {
    const flagged = { ...ledger, ledger: { ...ledger.ledger, honesty_notes: ['needs_attention:demand_charge_price:not_applicable'] } }
    expect(sectionStatuses({ study, ledger: flagged }).assumptions).toBe('needs_attention')
  })

  it('shows a run in progress and nothing yet for findings or report', () => {
    const s = sectionStatuses({ study, ledger, run: { ...(run as RunRecord), status: 'running' } })
    expect(s.run).toBe('running')
    expect(s.findings).toBe('not_started')
    expect(s.report).toBe('not_started')
  })
})
