// Template-e2e 22 (IMPROVE-MANUAL-1): the Improve card's "Open in Expert"
// mapper, checked on the actions the engine really emits and the request
// schema it really accepts (`__fixtures__/ehReviewActions.json`, built and
// kept current by backend/tests/test_eh_review_actions_fixture.py; GK B3 E2),
// and its one-shot, per-project, per-user hand-over (E3).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import type { EhReviewFinding } from '../../api/simulation'
import { buildEhStudyBody, EMPTY_PACK_FORM } from '../results/EhReferenceDesignPanel'
import { useUIStore } from '../../store/uiStore'
import { dropUserScopedQueries } from '../../auth/AuthProvider'
import fixture from './__fixtures__/ehReviewActions.json'
import {
  SHOWN_FIELDS, UNSHOWN_WORDS, improvePlan, openImproveInExpert, takeImprovePrefill,
  useImprovePrefillStore,
} from './improvePrefill'

type Fx = { case: string; id: string; severity: string; title: string; actions: EhReviewFinding['actions'] }
const findings = fixture.findings as Fx[]
const finding = (c: string): EhReviewFinding => {
  const f = findings.find(x => x.case === c)
  if (!f) throw new Error(`no fixture case ${c}`)
  return { ...f, severity: f.severity as EhReviewFinding['severity'], evidence: {}, recommendation: '' }
}
const ARCH = 'weak_flexible' as const

beforeEach(() => {
  useImprovePrefillStore.setState({ prefill: null })
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: 'hubDesign',
    resultsTabRequest: null, solverBlockRequest: null, flowReturn: null })
})

describe('the fixture is the engine’s', () => {
  it('covers both tools the review emits', () => {
    expect(new Set(findings.flatMap(f => f.actions.map(a => a.tool))))
      .toEqual(new Set(['run_eh_study', 'update_solver_config']))
  })

  it('every request field is shown or named in plain words (schema parity)', () => {
    const check = (fields: string[], shown: readonly string[], prefix: string) => {
      for (const k of fields) {
        if (shown.includes(k)) continue
        expect(UNSHOWN_WORDS[prefix + k], `${prefix}${k} is neither shown nor named`).toBeTruthy()
      }
    }
    check(fixture.schema.request, SHOWN_FIELDS.request, '')
    check(fixture.schema.pack_overrides, SHOWN_FIELDS.pack_overrides, 'pack_overrides.')
    check(fixture.schema.mc, SHOWN_FIELDS.mc, 'mc.')
    expect([...fixture.schema.levers].sort()).toEqual([...SHOWN_FIELDS.levers].sort())
    // Every shown name is a real schema field (no stale entry hides a gap).
    for (const k of SHOWN_FIELDS.request) expect(fixture.schema.request).toContain(k)
    for (const k of SHOWN_FIELDS.pack_overrides) expect(fixture.schema.pack_overrides).toContain(k)
    for (const k of SHOWN_FIELDS.mc) expect(fixture.schema.mc).toContain(k)
  })
})

describe('improvePlan on the real actions', () => {
  it('a re-run that keeps the goal fills the study form, field for field', () => {
    const plan = improvePlan(finding('fmea_dominant_mode'), ARCH)
    expect(plan.dest).toBe('study')
    expect(plan.goal).toBeUndefined()
    expect(plan.unshown).toEqual([])
    const args = finding('fmea_dominant_mode').actions[0].args
    // Round trip: the form builds the same request the action carries.
    const built = buildEhStudyBody(ARCH, plan.pack!).body!
    expect(built.pack_overrides).toEqual(args.pack_overrides)
    expect(built.stages).toEqual(args.stages)
    expect(built.budget_solves).toBe(args.budget_solves)
    expect(built.mc).toEqual(args.mc)
    expect(built.dsr_buses).toEqual(args.dsr_buses)
    expect(built.dtc_attribution).toBe(args.dtc_attribution)
  })

  it.each(['not_established_budget', 'budget_tight', 'dtc_critical_unserved',
    'certification_fail_outage_driven'])('%s round-trips through the study form', c => {
    const f = finding(c)
    const plan = improvePlan(f, ARCH)
    expect(plan.dest).toBe('study')
    expect(plan.unshown).toEqual([])
    const { archetype: _a, ...want } = f.actions[0].args as Record<string, unknown>
    void _a
    const { archetype: _b, ...got } = buildEhStudyBody(ARCH, plan.pack!).body!
    void _b
    expect(got).toEqual(want)
  })

  it('a tighter ENS target goes to the goal, in the action’s own unit (‱)', () => {
    const f = finding('certification_fail_energy_limited')
    const plan = improvePlan(f, ARCH)
    const po = f.actions[0].args.pack_overrides as Record<string, number>
    expect(plan.dest).toBe('goal')
    expect(plan.goal).toEqual({ ens_cap_permyriad: String(po.ens_cap_permyriad) })
    expect(plan.unshown).toEqual([])
  })

  it('more MC draws go to the goal’s draws; a goal change hands over no study form', () => {
    const f = finding('certification_inconclusive')
    const plan = improvePlan(f, ARCH)
    const mc = f.actions[0].args.mc as Record<string, number>
    expect(plan.dest).toBe('goal')
    expect(plan.goal).toEqual({ mc_draws: String(mc.draws) })
    expect(plan.pack).toBeUndefined()
  })

  it('a LOLE target implies its certification method; nothing is left unnamed', () => {
    const plan = improvePlan(finding('certification_no_target'), ARCH)
    expect(plan.dest).toBe('goal')
    expect(plan.goal).toEqual({ target_lole_h: '3' })
    expect(plan.unshown).toEqual([])
  })

  it('VOLL goes to Solver settings in €/MWh, the field’s unit', () => {
    const f = finding('not_established_voll')
    const plan = improvePlan(f, ARCH)
    expect(plan).toEqual({ dest: 'voll', voll: (f.actions[0].args.partial as { voll: number }).voll, unshown: [] })
  })

  it('names what the form cannot show instead of dropping it', () => {
    const f = finding('fmea_dominant_mode')
    const args = { ...f.actions[0].args, dtc_config: { x: 1 },
      mc: { draws: 10, cov_target: 0.1 },
      pack_overrides: { ...(f.actions[0].args.pack_overrides as object), frontier_default: true,
        certification_metric: 'none' } }
    const plan = improvePlan({ ...f, actions: [{ ...f.actions[0], args }] }, ARCH)
    expect(plan.unshown).toEqual(expect.arrayContaining([
      'the islanding stress settings', 'the sampling accuracy target',
      'whether the frontier runs by default', 'the certification method']))
  })

  it('a site-type mismatch, an off-type field or lever is named', () => {
    const f = finding('fmea_dominant_mode')
    const plan = improvePlan(f, 'off_grid')
    expect(plan.unshown).toEqual(expect.arrayContaining([
      'a different site type', 'an import limit', 'the flexible-demand buses']))
    expect(plan.pack?.importMw).toBe('')
    const lever = { ...f, actions: [{ ...f.actions[0], args: { archetype: 'off_grid',
      pack_overrides: { levers: { import_cap: true } } } }] }
    expect(improvePlan(lever, 'off_grid').unshown).toEqual(['a lever this site type does not offer'])
  })

  it('a finding with no action opens its place, with nothing to fill', () => {
    const bare = (id: string): EhReviewFinding => ({ id, severity: 'high', title: 't', evidence: {}, recommendation: '', actions: [] })
    expect(improvePlan(bare('ens_target_missed'), ARCH)).toEqual({ dest: 'bounds', unshown: [] })
    expect(improvePlan(bare('scr_warn'), ARCH)).toEqual({ dest: 'study', unshown: [] })
  })

  it('a VOLL action without a number names the missing value', () => {
    const f: EhReviewFinding = { id: 'x', severity: 'medium', title: 't', evidence: {}, recommendation: '',
      actions: [{ tool: 'update_solver_config', args: { partial: { voll: 'high' } }, effect: '' }] }
    expect(improvePlan(f, ARCH)).toEqual({ dest: 'voll', unshown: ['the value to set'] })
  })
})

describe('the hand-over (GK B3 E1, E3)', () => {
  it('Open in Expert writes nothing: it only hands over and navigates', async () => {
    const { default: client } = await import('../../api/client')
    const spies = (['post', 'put', 'patch', 'delete'] as const).map(m => vi.spyOn(client, m))
    for (const c of ['not_established_voll', 'fmea_dominant_mode', 'certification_inconclusive']) {
      openImproveInExpert('Set VOLL', improvePlan(finding(c), ARCH))
    }
    // Let any deferred write (a microtask or timer) land before asserting.
    await new Promise(r => setTimeout(r, 0))
    await Promise.resolve()
    for (const s of spies) expect(s).not.toHaveBeenCalled()
    useImprovePrefillStore.setState({ prefill: null })
    openImproveInExpert('Set VOLL', improvePlan(finding('not_established_voll'), ARCH))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('expert')
    expect(ui.activeSlidePanel).toBe('simparams')
    expect(ui.solverBlockRequest).toBe('voll')
    expect(ui.flowReturn).not.toBeNull()
    expect(useImprovePrefillStore.getState().prefill).toEqual({ project: 'Demo', title: 'Set VOLL', voll: 5000 })
  })

  it('a study plan opens Results › Adequacy', () => {
    openImproveInExpert('Spare', improvePlan(finding('fmea_dominant_mode'), ARCH))
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('results')
    expect(ui.resultsTabRequest).toBe('adequacy')
  })

  it('each part is taken once', () => {
    useImprovePrefillStore.getState().set({ project: 'Demo', title: 'T', goal: { mc_draws: '1000' },
      pack: { ...EMPTY_PACK_FORM, seed: '7' } })
    expect(takeImprovePrefill('Demo', 'solver')).toEqual({ title: 'T', goal: { mc_draws: '1000' } })
    expect(takeImprovePrefill('Demo', 'solver')).toBeNull()
    expect(takeImprovePrefill('Demo', 'pack')?.pack.seed).toBe('7')
    expect(takeImprovePrefill('Demo', 'pack')).toBeNull()
    expect(useImprovePrefillStore.getState().prefill).toBeNull()
  })

  it('another project never reads it, and the attempt drops it', () => {
    openImproveInExpert('T', improvePlan(finding('fmea_dominant_mode'), ARCH))
    expect(takeImprovePrefill('Other', 'pack')).toBeNull()
    expect(useImprovePrefillStore.getState().prefill).toBeNull()
    expect(takeImprovePrefill(null, 'pack')).toBeNull()
  })

  it('a project switch drops it', () => {
    openImproveInExpert('T', improvePlan(finding('fmea_dominant_mode'), ARCH))
    useUIStore.getState().setCurrentProject('Other')
    expect(useImprovePrefillStore.getState().prefill).toBeNull()
  })

  it('a user switch drops it', () => {
    openImproveInExpert('T', improvePlan(finding('fmea_dominant_mode'), ARCH))
    dropUserScopedQueries(new QueryClient())
    expect(useImprovePrefillStore.getState().prefill).toBeNull()
  })

  it('a plan with nothing to fill hands nothing over', () => {
    openImproveInExpert('T', { dest: 'bounds', unshown: [] })
    expect(useImprovePrefillStore.getState().prefill).toBeNull()
    expect(useUIStore.getState().activeSlidePanel).toBe('capacityBounds')
  })

  it('the empty form is not a prefill', () => {
    expect(EMPTY_PACK_FORM.levers).toEqual({})
  })
})

// Hand-mutant survivors (GK B3 E6), each pinned by one assertion.
describe('mapper edges', () => {
  const rerun = (args: Record<string, unknown>): EhReviewFinding => ({ id: 'x', severity: 'medium', title: 't',
    evidence: {}, recommendation: '', actions: [{ tool: 'run_eh_study', args, effect: '' }] })
  const solver = (partial: Record<string, unknown>): EhReviewFinding => ({ id: 'x', severity: 'medium', title: 't',
    evidence: {}, recommendation: '', actions: [{ tool: 'update_solver_config', args: { partial }, effect: '' }] })

  it('a certification method without its LOLE target, or "none" with one, is named', () => {
    expect(improvePlan(rerun({ pack_overrides: { certification_metric: 'mc_lole' } }), ARCH).unshown)
      .toContain('the certification method')
    expect(improvePlan(rerun({ pack_overrides: { certification_metric: 'none', target_lole_h: 3 } }), ARCH).unshown)
      .toContain('the certification method')
  })

  it('off weak_flexible, an import energy budget is named; empty DSR buses are not', () => {
    expect(improvePlan(rerun({ pack_overrides: { import_energy_mwh_per_year: 100 } }), 'strong_grid').unshown)
      .toEqual(['an import energy budget'])
    expect(improvePlan(rerun({ dsr_buses: [] }), 'strong_grid').unshown).toEqual([])
    expect(improvePlan(rerun({ dsr_buses: ['b1'] }), 'strong_grid').pack?.dsrBuses).toBe('')
  })

  it('a non-boolean lever value is ignored', () => {
    expect(improvePlan(rerun({ pack_overrides: { levers: { redundancy: 'yes' } } }), ARCH).pack?.levers).toEqual({})
  })

  it('each unshown phrase appears once', () => {
    const plan = improvePlan(rerun({ foo: 1, bar: 2 }), ARCH)
    expect(plan.unshown).toEqual(['a setting this screen does not show'])
  })

  it('VOLL with another solver setting names it; a non-finite VOLL is not filled in', () => {
    expect(improvePlan(solver({ voll: 1, solver_name: 'x' }), ARCH).unshown).toEqual(['another solver setting'])
    expect(improvePlan(solver({ voll: Infinity }), ARCH)).toEqual({ dest: 'voll', unshown: ['the value to set'] })
  })

  it('taking the form leaves the goal for Solver settings; a goal-only prefill gives no form', () => {
    useImprovePrefillStore.getState().set({ project: 'Demo', title: 'T', goal: { mc_draws: '1000' },
      pack: { ...EMPTY_PACK_FORM, seed: '7' } })
    expect(takeImprovePrefill('Demo', 'pack')).not.toBeNull()
    expect(takeImprovePrefill('Demo', 'solver')).toEqual({ title: 'T', goal: { mc_draws: '1000' } })
    useImprovePrefillStore.getState().set({ project: 'Demo', title: 'G', goal: { mc_draws: '9' } })
    expect(takeImprovePrefill('Demo', 'pack')).toBeNull()
    expect(useImprovePrefillStore.getState().prefill?.goal).toEqual({ mc_draws: '9' })
  })
})
