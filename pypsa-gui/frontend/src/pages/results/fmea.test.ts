import { describe, expect, it } from 'vitest'
import { buildManualRow, mergeWorksheet, unpricedRankingWarning, WORKSHEET_CSV_HEADER, worksheetCsvRows, ZERO_REASON_TEXT } from './fmea'
import type { CoptPayload } from './adequacy'

const copt: CoptPayload = {
  engine: 'copt', fidelity: 'analytic_convolution',
  metrics: { lole_hours: 1, eue_mwh: 2, lolp_max: 0.1, time_basis: 'hours_per_year' },
  fleet: { units: 2, must_take: 0, delta_mw: 1 },
  voll_eur_per_mwh: 3000,
  per_mode: [
    { mode_id: 'generator:g1:forced_outage', component_class: 'Generator',
      name: 'g1', failure_class: 'A', occurrence_per_year: 8, occurrence_basis: 'EFORd',
      severity_eur: 1000, criticality_eur_per_year: 8000, delta_eue_mwh: 2.6,
      in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution' },
    { mode_id: 'generator:g2:forced_outage', component_class: 'Generator',
      name: 'g2', failure_class: 'A', occurrence_per_year: 4, occurrence_basis: 'EFORd',
      severity_eur: 500, criticality_eur_per_year: 2000, delta_eue_mwh: 0.6,
      in_metric_scope: true, engine: 'copt', fidelity: 'analytic_convolution' },
  ],
}

const sidecar = {
  version: 3,
  manual_rows: [{
    mode_id: 'manual:cyber', component_class: 'Network', name: 'cyber',
    failure_class: 'D', occurrence_per_year: 0.5, occurrence_basis: 'expert',
    severity_eur: 10000, criticality_eur_per_year: 5000, in_metric_scope: false,
    mitigability: 'segmentation', engine: 'expert', fidelity: 'expert_judgement',
  }],
  overlays: { 'generator:g1:forced_outage': { mitigability: 'N-1 reserve' } },
}

describe('mergeWorksheet', () => {
  it('interleaves computed and manual rows on one criticality ranking', () => {
    const rows = mergeWorksheet(copt, sidecar)
    expect(rows.map(r => r.name)).toEqual(['g1', 'cyber', 'g2'])
    expect(rows.map(r => r.editable)).toEqual([false, true, false])
  })
  it('re-attaches overlays to computed rows by mode_id', () => {
    const rows = mergeWorksheet(copt, sidecar)
    expect(rows.find(r => r.name === 'g1')?.mitigability).toBe('N-1 reserve')
    expect(rows.find(r => r.name === 'g2')?.mitigability).toBe('')
  })
  it('is empty-safe on 204s from either side', () => {
    expect(mergeWorksheet(null, null)).toEqual([])
    expect(mergeWorksheet(copt, null)).toHaveLength(2)
    expect(mergeWorksheet(null, sidecar)).toHaveLength(1)
  })
})

describe('worksheetCsvRows', () => {
  it('matches the IEC 60812-shaped header column for column', () => {
    const rows = worksheetCsvRows(mergeWorksheet(copt, sidecar))
    expect(rows[0]).toHaveLength(WORKSHEET_CSV_HEADER.length)
    const g1 = rows[0]
    expect(g1[WORKSHEET_CSV_HEADER.indexOf('criticality_eur_per_year')]).toBe(8000)
    expect(g1[WORKSHEET_CSV_HEADER.indexOf('mitigability')]).toBe('N-1 reserve')
    expect(g1[WORKSHEET_CSV_HEADER.indexOf('engine')]).toBe('copt')
  })
  it('has no RPN and no Action Priority column', () => {
    expect(WORKSHEET_CSV_HEADER.join(',')).not.toMatch(/rpn|action_priority/i)
  })
})

describe('buildManualRow', () => {
  it('computes criticality as occurrence × severity and labels provenance', () => {
    const r = buildManualRow({ name: 'Fuel supply loss', occurrencePerYear: 0.2, severityEur: 5000 })
    expect(r.criticality_eur_per_year).toBe(1000)
    expect(r.engine).toBe('expert')
    expect(r.fidelity).toBe('expert_judgement')
    expect(r.failure_class).toBe('D')
    expect(r.mode_id).toBe('manual:fuel_supply_loss')
  })
  it('clamps negatives to zero (the contract forbids negative criticality)', () => {
    const r = buildManualRow({ name: 'x', occurrencePerYear: -1, severityEur: 100 })
    expect(r.occurrence_per_year).toBe(0)
    expect(r.criticality_eur_per_year).toBe(0)
  })
})


describe('mergeWorksheet across computed classes (Phase 4)', () => {
  it('interleaves A, B and parametric C rows on one ranking', () => {
    const modes = {
      per_mode: [
        { mode_id: 'generator:g1:forced_outage', component_class: 'Generator',
          name: 'g1', failure_class: 'A', occurrence_per_year: 8,
          occurrence_basis: 'EFORd', severity_eur: 100,
          criticality_eur_per_year: 800, in_metric_scope: true,
          engine: 'copt', fidelity: 'analytic_convolution' },
        { mode_id: 'link:tie:forced_outage', component_class: 'Link',
          name: 'tie', failure_class: 'B', occurrence_per_year: 7.3,
          occurrence_basis: 'FOR', severity_eur: 500,
          criticality_eur_per_year: 3650, in_metric_scope: true,
          engine: 'lp_proxy', fidelity: 'deterministic_scenario' },
        { mode_id: 'scenario:cold_snap', component_class: 'Network',
          name: '1-in-20 cold snap', failure_class: 'C',
          occurrence_per_year: 0.05, occurrence_basis: 'scenario:parametric',
          severity_eur: 40000, criticality_eur_per_year: 2000,
          in_metric_scope: true,
          engine: 'lp_proxy', fidelity: 'deterministic_scenario' },
      ],
      sweep_status: 'done',
    }
    const rows = mergeWorksheet(modes, null)
    expect(rows.map(r => r.failure_class)).toEqual(['B', 'C', 'A'])
    // The parametric label rides in the occurrence basis for the UI.
    expect(rows[1].occurrence_basis).toContain('parametric')
  })
})

describe('unpricedRankingWarning', () => {
  const rows = [{ mode_id: 'g:a:forced_outage', criticality_eur_per_year: 0 }]

  it('warns when VoLL is unset, because every criticality is then zero', () => {
    const w = unpricedRankingWarning({ per_mode: rows, voll_eur_per_mwh: 0 })
    expect(w).toContain('Value of Lost Load')
  })

  it('stays silent once the ranking is actually priced', () => {
    expect(unpricedRankingWarning({ per_mode: rows, voll_eur_per_mwh: 3000 })).toBeNull()
  })

  it('says nothing when there are no rows to rank', () => {
    expect(unpricedRankingWarning({ per_mode: [], voll_eur_per_mwh: 0 })).toBeNull()
  })

  it('stays silent when the payload omits VoLL, rather than guessing', () => {
    expect(unpricedRankingWarning({ per_mode: rows })).toBeNull()
    expect(unpricedRankingWarning(null)).toBeNull()
  })
})

// P29 (B3): `zero_reason` rides from per_mode onto the row; manual rows carry
// null; an unknown value is dropped. The CSV is byte-stable: same header,
// same columns, no reason column.
describe('zero_reason', () => {
  const withReasons: CoptPayload = { ...copt, per_mode: [
    { ...copt.per_mode[0], severity_eur: 0, criticality_eur_per_year: 0, zero_reason: 'no_shortfall' },
    { ...copt.per_mode[1], zero_reason: 'bogus' },
  ] }
  it('is forwarded for computed rows and null for manual rows', () => {
    const rows = mergeWorksheet(withReasons, sidecar)
    expect(rows.find(r => r.name === 'g1')?.zero_reason).toBe('no_shortfall')
    expect(rows.find(r => r.name === 'g2')?.zero_reason ?? null).toBeNull()
    expect(rows.find(r => r.name === 'cyber')?.zero_reason).toBeNull()
  })
  it('has one text per reason', () => {
    expect(ZERO_REASON_TEXT).toEqual({
      no_shortfall: 'no shortfall — the site copes without it',
      no_outage_data: 'no outage rate set (or it is zero)',
      unpriced: 'no price set for undelivered energy',
      out_of_scope: 'not counted (outside the electricity metric)',
    })
  })
  it('leaves the CSV unchanged', () => {
    expect(WORKSHEET_CSV_HEADER).toEqual([
      'mode_id', 'failure_class', 'component', 'name',
      'occurrence_per_year', 'occurrence_basis',
      'severity_eur', 'criticality_eur_per_year', 'delta_eue_mwh',
      'in_metric_scope', 'mitigability', 'engine', 'fidelity',
    ])
    const strip = (p: CoptPayload): CoptPayload => ({ ...p, per_mode: p.per_mode.map(m => {
      const { zero_reason: _z, ...rest } = m as Record<string, unknown>
      return rest as typeof m
    }) })
    expect(worksheetCsvRows(mergeWorksheet(withReasons, sidecar)))
      .toEqual(worksheetCsvRows(mergeWorksheet(strip(withReasons), sidecar)))
  })
})
