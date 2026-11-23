// Guided-mode spec §5.5: the Results card's plain-language headline, one
// sentence per outcome. Numbers are the review's (summary / evidence) and the
// report's, never computed anew.
import { describe, expect, it } from 'vitest'
import type { EhReferenceDesignReport, EhReview } from '../../api/simulation'
import { headline, riskName } from './headline'

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
          { mode_id: 'B:grid_import', name: 'grid_import' },
          { mode_id: 'B:x', name: 'second' }] } },
      },
    })
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 12.38, target_lole_h: 3 }), r))
      .toBe('Not certified: about 12.4 h/yr of shortfall vs a 3 h/yr goal, driven by Grid import')
  })

  it('fail without ranked risks: driven by the plan\'s energy limit', () => {
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 7.6, target_lole_h: 2.5 }), report()))
      .toBe('Not certified: about 7.6 h/yr of shortfall vs a 2.5 h/yr goal, driven by the plan\'s energy limit')
  })

  it('fail: a row with no name falls back to its mode id', () => {
    const r = report({ sections: { fmea_top: { status: 'ok', payload: { rows: [{ mode_id: 'B:tie' }] } } } })
    expect(headline(review({ verdict: 'fail', mc_lole_h_per_year: 4, target_lole_h: 3 }), r))
      .toMatch(/, driven by B:tie$/)
  })

  it('inconclusive: the per-year interval straddles the goal', () => {
    const r = report({ ...cert({ verdict: 'inconclusive', lole_h_per_year: 3.1,
      lole_ci: [4.4, 7.8], horizon_years: 2, target_lole_h: 3 }) })
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 3.1, target_lole_h: 3 }, [{
      id: 'certification_inconclusive', severity: 'medium', title: 't', recommendation: 'r', actions: [],
      evidence: { verdict: 'inconclusive', lole_ci_per_horizon: [4.4, 7.8] } }])
    expect(headline(rv, r)).toBe(
      'Not decided: the shortfall estimate (2.2–3.9 h/yr) straddles the 3 h/yr goal: more simulation runs would settle it.')
  })

  it('inconclusive without a horizon: the evidence interval is read as is', () => {
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 3, target_lole_h: 3 }, [{
      id: 'certification_inconclusive', severity: 'medium', title: 't', recommendation: 'r', actions: [],
      evidence: { lole_ci_per_horizon: [1.6, 4.2] } }])
    expect(headline(rv, report())).toBe(
      'Not decided: the shortfall estimate (1.6–4.2 h/yr) straddles the 3 h/yr goal: more simulation runs would settle it.')
  })

  // template-e2e 21: a goal under the resolution floor is not a straddle; the
  // sentence names the floor and the fewest runs that resolve the goal.
  it('inconclusive below the resolution floor: names the floor and the minimum runs', () => {
    const r = report({ ...cert({ verdict: 'inconclusive', lole_h_per_year: 0, lole_ci: [0, 0],
      horizon_years: 2, target_lole_h: 3, resolution_floor_h: 104.2, n_samples: 1,
      below_floor: true, min_draws: 18 }) })
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 0, target_lole_h: 3 })
    expect(headline(rv, r)).toBe(
      'Not decided: with 1 simulation run, the smallest shortfall it can show is 52.1 h/yr, more than your 3 h/yr goal. Set at least 18 runs per check (section 8, Scenarios & optimisation) to decide it.')
  })

  it('below the floor with a zero goal: no run count settles it', () => {
    const r = report({ ...cert({ verdict: 'inconclusive', lole_h_per_year: 0, lole_ci: [0, 0],
      horizon_years: 1, target_lole_h: 0, resolution_floor_h: 0.104, n_samples: 500,
      below_floor: true, min_draws: null }) })
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 0, target_lole_h: 0 })
    expect(headline(rv, r)).toBe(
      'Not decided: with 500 simulation runs, the smallest shortfall it can show is 0.104 h/yr, and a 0 h/yr goal allows none. Allow some shortfall (section 8, Scenarios & optimisation) to decide it.')
  })

  it('inconclusive held by a feeder bound names the feeder, not more runs (GK A F6\')', () => {
    // The interval (≈ 10–21 h/yr) sits under the 25 h/yr goal: it does not straddle.
    const r = report({ ...cert({ verdict: 'inconclusive', lole_h_per_year: 15,
      lole_ci: [10, 21], horizon_years: 1, target_lole_h: 25 }) })
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 15, target_lole_h: 25,
      feeder_bound: { pockets: [{ buses: ['it_bus'], feeders: ['site_transformer'],
        bound_h_per_year: 43.8 }], chained: [] } })
    const text = headline(rv, r)
    expect(text).toMatch(/^Not decided: IT bus could be cut off up to 43\.8 h\/yr while Site transformer is out/)
    expect(text).toMatch(/second feeder/)
    expect(text).not.toMatch(/straddles|simulation runs|it_bus/)
  })

  it('a bound held by a feeder without outage data asks for its data (GK R2)', () => {
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 0, target_lole_h: 3,
      feeder_bound: { pockets: [{ buses: ['crit'], feeders: ['crit_feeder'], no_data: ['crit_feeder'],
        bound_h_per_year: 8760 }], chained: [] } })
    const text = headline(rv, report())
    // UX 2 (4): cause first, no bracket, three sentences.
    expect(text).toBe('Not decided: Crit could be cut off up to 8760.0 h/yr while Crit feeder is out, '
      + 'against the 3 h/yr goal. Crit feeder\'s outage rate is not known, so it is taken as always out. '
      + 'Outage data for Crit feeder, a second feeder or a more reliable one would settle it.')
  })

  it('inconclusive held by a chained feeder says it cannot be bounded', () => {
    const rv = review({ verdict: 'inconclusive', mc_lole_h_per_year: 1, target_lole_h: 3,
      feeder_bound: { pockets: [], chained: ['crit_feeder'] } })
    expect(headline(rv, report())).toMatch(/^Not decided: Crit feeder reaches .* cannot bound/)
  })

  it('pass: one decimal, under the goal', () => {
    expect(headline(review({ verdict: 'pass', mc_lole_h_per_year: 0.44, target_lole_h: 3 }), report()))
      .toBe('Certified: about 0.4 h/yr of shortfall, under the 3 h/yr goal.')
  })

  it('a computed zero says no shortfall, never "about 0.0 h/yr" (UX audit M7)', () => {
    expect(headline(review({ verdict: 'pass', mc_lole_h_per_year: 0, target_lole_h: 3 }), report()))
      .toBe('Certified: no shortfall expected, under the 3 h/yr goal.')
    expect(headline(review({ verdict: 'pass', mc_lole_h_per_year: 0.04, target_lole_h: 3 }), report()))
      .toBe('Certified: no shortfall expected, under the 3 h/yr goal.')
    expect(headline(review({ verdict: null, mc_lole_h_per_year: 0, target_lole_h: null }), report()))
      .toBe('No reliability goal was set: the study expects no shortfall. Set a goal to certify.')
  })

  it('no target: the study reports the shortfall and asks for a goal', () => {
    expect(headline(review({ verdict: null, mc_lole_h_per_year: 1.25, target_lole_h: null }), report()))
      .toBe('No reliability goal was set: the study reports 1.3 h/yr of shortfall. Set a goal to certify.')
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
      .toBe('No reliability goal is set for this site, so the study did not certify it: set an allowed shortfall in section 8, Scenarios & optimisation, to get a verdict.')
  })

  it('reads the report when the review summary lacks the shortfall (mc_lole_h)', () => {
    expect(headline(review({ verdict: 'pass', target_lole_h: 3 }), report({ mc_lole_h: 1.04 })))
      .toBe('Certified: about 1.0 h/yr of shortfall, under the 3 h/yr goal.')
  })
})

describe('riskName (UX S6, G1)', () => {
  it('a component id in plain words', () => {
    expect(riskName('grid_import')).toBe('Grid import')
    expect(riskName('genset_1')).toBe('Genset 1')
  })
  it('a common-mode outage names its links', () => {
    expect(riskName('common_mode:grid_import,site_transformer'))
      .toBe('Grid import and Site transformer failing together')
  })
  it('a name a person typed is kept', () => {
    expect(riskName('Main feeder')).toBe('Main feeder')
  })
})

describe('headline and Expert share one set of verdict words (EXPERT-GUIDED-1)', () => {
  it.each([['pass', 'Certified:'], ['fail', 'Not certified:'], ['inconclusive', 'Not decided:']])(
    '%s opens with "%s"', (verdict, words) => {
      const r = report({ ...cert({ verdict, target_lole_h: 3, lole_h_per_year: 4, lole_ci: [2, 6] }),
        mc_lole_h: 4 })
      expect(headline(review({ verdict, mc_lole_h_per_year: 4, target_lole_h: 3 }), r)
        .startsWith(words)).toBe(true)
    })
})
