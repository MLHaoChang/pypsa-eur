// Shared fixtures for the hub-design card tests (not imported by app code).
import type {
  EhReadiness, EhReferenceDesignReport, EhReview, EhTemplateMeta,
} from '../../api/simulation'

export const DC_TEMPLATE: EhTemplateMeta = {
  id: 'eh_datacenter',
  name: 'Data Center Energy Hub',
  recommended_archetype: 'weak_flexible',
  pack_overrides: { import_p_nom_mw: 40 },
  stages: null,
  study_notes: ['The grid connection is capped at 40 MW.', 'Second note.'],
  provenance: 'Synthetic example data — not a real site.',
}

export const MG_TEMPLATE: EhTemplateMeta = {
  id: 'eh_microgrid',
  name: 'Island Microgrid',
  recommended_archetype: 'off_grid',
  pack_overrides: {},
  stages: null,
  study_notes: ['Runs without its subsea tie.'],
  provenance: 'Synthetic example data — not a real site.',
}

export function readiness(over: Partial<EhReadiness> = {}): EhReadiness {
  return {
    archetype: 'weak_flexible',
    import: { rule: 'eh_role', links: ['grid_import'], applied: true },
    critical_buses: ['it_bus'],
    dtc: { derivable: true, reason: null },
    scr: { status: 'ok', note: null, min_scr: 3.2 },
    storage_units: 2,
    class_b: { k: 1, closed_import_links: [], error: null },
    mc_boundary: { ok: true, error: null },
    budget_solves: 40,
    estimated_solves: 20,
    stages: [],
    warnings: [],
    pack_defaults: { target_lole_h: 3, ens_cap_permyriad: 10, certification_metric: 'mc_lole' },
    outage_units: { count: 9, by_class: { Generator: 5, Link: 2, StorageUnit: 2 }, missing: [] },
    import_p_nom_mw: 40,
    ...over,
  }
}

export const REPORT: EhReferenceDesignReport = {
  archetype: 'weak_flexible',
  pack_hash: 'h',
  assumptions_hash: 'a',
  mc_lole_h: 12.38,
  cost_at_target_eur: 12_345_678,
  completeness: { target: 'ok', frontier: 'not_established', certification: 'ok' },
  sections: {
    certification: { status: 'ok', payload: { verdict: 'fail', lole_h_per_year: 12.38, target_lole_h: 3 } },
    frontier: { status: 'not_established', note: 'VOLL must be above zero for the frontier.' },
    fmea_top: { status: 'ok', payload: { rows: [
      { mode_id: 'B:grid_import', name: 'grid_import', criticality_eur_per_year: 590_000 },
      { mode_id: 'B:genset_1', name: 'genset_1', criticality_eur_per_year: 120_000 },
      { mode_id: 'B:genset_2', name: 'genset_2', criticality_eur_per_year: 90_000 },
      { mode_id: 'B:pv', name: 'pv', criticality_eur_per_year: 1_000 },
    ] } },
  },
}

export function review(over: Partial<Extract<EhReview, { status: 'ok' }>> = {}):
  Extract<EhReview, { status: 'ok' }> {
  return {
    status: 'ok',
    source: 'stored report',
    stale: false,
    summary: { verdict: 'fail', mc_lole_h_per_year: 12.38, target_lole_h: 3,
      cost_at_target_eur: 12_345_678 },
    findings: [
      { id: 'certification_fail', severity: 'high',
        title: 'Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target',
        evidence: { verdict: 'fail', lole_h_per_year: 12.38, target_lole_h: 3 },
        recommendation: 'Add firm local capacity.',
        actions: [{ tool: 'run_eh_study', args: { archetype: 'weak_flexible',
          stages: ['apply_pack', 'ens_solve', 'dtc_stress', 'dtc_planning', 'assemble'] },
          effect: 'size the local capacity' }] },
      { id: 'not_established_frontier', severity: 'medium', title: 'frontier: not established',
        evidence: { section: 'frontier', note: 'VOLL' }, recommendation: 'Set VOLL.',
        actions: [] },
      { id: 'budget_tight', severity: 'low', title: 'Budget nearly spent',
        evidence: {}, recommendation: 'More budget.', actions: [] },
      { id: 'frontier_knee', severity: 'info', title: 'Knee', evidence: {},
        recommendation: 'Info.', actions: [] },
    ],
    next_steps: [],
    ...over,
  }
}
