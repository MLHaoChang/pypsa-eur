// Guided-mode spec §5.5: the Results card's plain-language headline, one
// sentence per outcome. Numbers are the review's (summary / evidence) and the
// report's, never computed anew.
import { describe, expect, it } from 'vitest'
import type { EhReferenceDesignReport, EhReview } from '../../api/simulation'
import { headline } from './headline'

type OkReview = Extract<EhReview, { status: 'ok' }>

function review(summary: OkReview['summary'], findings: OkReview['findings'] = []): OkReview {
  return { status: 'ok', source: 'stored report', stale: false, summary, findings, next_steps: [] }
}

function report(extra: Partial<EhReferenceDesignReport> = {}): EhReferenceDesignReport {
  return { archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a', ...extra }
}

const cert = (payload: Record<string, unknown>, note?: string) => ({
  sections: { certification: { status: 'ok' as const, payload, note: note ?? null } },
})

describe('headline (§5.5)', () => {
  it('fail: rounded shortfall vs the goal, driven by the top FMEA mode', () => {
    const r = report({
      mc_lole_h: 12.38,
      sections: {
        certification: { status: 'ok', payload: { verdict: 'fail', lole_h_per_year: 12.38, target_lole_h: 3 } },
        fmea_top: { status: 'ok', payload: { rows: [
          { mode_id: 'B:grid_import', name: 'grid_import outage' },
          { mode_id: 'B:x', name: 'second' }] } },
      },
    })
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 12.38, target_lole_h: 3 }), r))
      .toBe('Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal — driven by grid_import outage')
  })

  it('fail without ranked risks: driven by the plan\'s energy limit', () => {
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 7.6, target_lole_h: 2.5 }), report()))
      .toBe('Not certified: about 8 h/yr of shortfall vs a 2.5 h/yr goal — driven by the plan\'s energy limit')
  })

  it('fail: a row with no name falls back to its mode id', () => {
    const r = report({ sections: { fmea_top: { status: 'ok', payload: { rows: [{ mode_id: 'B:tie' }] } } } })
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 4, target_lole_h: 3 }), r))
      .toMatch(/— driven by B:tie$/)
  })

  it('inconclusive: the per-year interval straddles the goal', () => {
    const r = report({ ...cert({ verdict: 'inconclusive', lole_h_per_year: 3.1,
      lole_ci: [4.4, 7.8], horizon_years: 2, target_lole_h: 3 }) })
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 3.1, target_lole_h: 3 }, [{
      id: 'certification_inconclusive', severity: 'medium', title: 't', recommendation: 'r', actions: [],
      evidence: { verdict: 'inconclusive', lole_ci_per_horizon: [4.4, 7.8] } }])
    expect(headline(rv, r)).toBe(
      'Not decided: the shortfall estimate (2–4 h/yr) straddles the 3 h/yr goal — more simulation runs would settle it.')
  })

  it('inconclusive without a horizon: the evidence interval is read as is', () => {
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 3, target_lole_h: 3 }, [{
      id: 'certification_inconclusive', severity: 'medium', title: 't', recommendation: 'r', actions: [],
      evidence: { lole_ci_per_horizon: [1.6, 4.2] } }])
    expect(headline(rv, report())).toBe(
      'Not decided: the shortfall estimate (2–4 h/yr) straddles the 3 h/yr goal — more simulation runs would settle it.')
  })

  it('pass: one decimal, under the goal', () => {
    expect(headline(review({ verdict: 'pass', mc_lole_h_per_year: 0.44, target_lole_h: 3 }), report()))
      .toBe('Certified: about 0.4 h/yr of shortfall, under the 3 h/yr goal.')
  })

  it('no target: the study reports the shortfall and asks for a goal', () => {
    expect(headline(review({ verdict: null, mc_lole_h_per_year: 1.25, target_lole_h: null }), report()))
      .toBe('No reliability goal was set — the study reports 1.3 h/yr of shortfall. Set a goal to certify.')
  })

  it('not established: the certification note, verbatim', () => {
    const r = report({ completeness: { certification: 'not_established' },
      sections: { certification: { status: 'not_established', note: 'no hub-side buses behind the boundary' } } })
    expect(headline(review({ verdict: null, mc_lole_h_per_year: null, target_lole_h: 3 }), r))
      .toBe('The study could not certify reliability: no hub-side buses behind the boundary')
  })

  it('MC not run with a goal: a plain reason instead of an empty note', () => {
    expect(headline(review({ verdict: null, mc_lole_h_per_year: null, target_lole_h: 3 }), report()))
      .toBe('The study could not certify reliability: the reliability check was not part of this run.')
  })

  it('no goal and no shortfall number (decided at P24-FE): ask for a goal, not the engine note', () => {
    const r = report({ completeness: { certification: 'skipped' },
      sections: { certification: { status: 'skipped', note: 'no LOLE target — certification not requested' } } })
    expect(headline(review({ verdict: null, mc_lole_h_per_year: null, target_lole_h: null }), r))
      .toBe('No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict.')
  })

  it('reads the report when the review summary lacks the shortfall (mc_lole_h)', () => {
    expect(headline(review({ verdict: 'pass', target_lole_h: 3 }), report({ mc_lole_h: 1.04 })))
      .toBe('Certified: about 1.0 h/yr of shortfall, under the 3 h/yr goal.')
  })
})
