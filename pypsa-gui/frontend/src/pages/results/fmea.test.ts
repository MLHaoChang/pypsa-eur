import { describe, expect, it } from 'vitest'
import { buildManualRow, mergeWorksheet, WORKSHEET_CSV_HEADER, worksheetCsvRows } from './fmea'
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
