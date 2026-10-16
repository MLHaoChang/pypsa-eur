// The Guided report entry (UX S6, G4/G6): one tile per category, each a figure
// or a state in words, never 0 or "—" for what did not run.
import { describe, expect, it } from 'vitest'
import { chapterTiles, type ChapterInputs } from './chapters'
import type { InvestmentCaseReportPayload } from '../../api/types'

const NONE: ChapterInputs = {
  investment: { study: null, report: null },
  grid: { campus: null, notHub: false },
  reliability: { hasResults: false, running: false, lole: null, target: null, verdict: null },
  operation: { status: null, gfm: null },
}

const byId = (inputs: ChapterInputs) => Object.fromEntries(chapterTiles(inputs).map(t => [t.id, t]))

describe('chapterTiles', () => {
  it('four tiles, in the category order, named by the result groups', () => {
    expect(chapterTiles(NONE).map(t => [t.id, t.group])).toEqual([
      ['investment', 'Investment'], ['grid_plan', 'Grid plan'],
      ['reliability', 'Reliability'], ['operation', 'Operation']])
  })

  it('nothing run: each says not run and where to start, never a figure', () => {
    const t = byId(NONE)
    for (const tile of Object.values(t)) {
      expect(tile.state).toBe('not_run')
      expect(tile.value).toBe('')
      expect(tile.reason).toBeTruthy()
    }
    expect(t.reliability.open).toEqual({ step: 'goal' })
    expect(t.investment.open).toEqual({ panel: 'results', tab: 'investment' })
    expect(t.grid_plan.open).toEqual({ panel: 'campusElectrical' })
    expect(t.operation.open).toEqual({ panel: 'results', tab: 'dispatch' })
  })

  it('reliability: the shortfall against the goal', () => {
    const t = byId({ ...NONE, reliability: { hasResults: true, running: false, lole: 12.38,
                                             target: 3, verdict: 'fail' } })
    expect(t.reliability).toMatchObject({ state: 'ready', value: '12.38', unit: 'h/yr',
      basis: 'Not certified: the goal is 3 h/yr', open: { panel: 'results', tab: 'adequacy' } })
  })

  it('reliability: running, and a run with no shortfall figure', () => {
    expect(byId({ ...NONE, reliability: { ...NONE.reliability, running: true } }).reliability.state)
      .toBe('running')
    const t = byId({ ...NONE, reliability: { hasResults: true, running: false, lole: null,
                                             target: null, verdict: null } })
    expect(t.reliability.state).toBe('unavailable')
  })

  it('investment: the post-tax NPV when established, else the pre-tax one, said so', () => {
    const report = (npv: number | null, pre: number | null) => ({
      npv_at_wacc: npv,
      sections: { project: { status: 'ok', payload: { project_pre_tax_npv: pre } } },
    }) as unknown as InvestmentCaseReportPayload
    const done = { status: 'done' } as never
    let t = byId({ ...NONE, investment: { study: done, report: report(-1.2e6, -1e6) } })
    expect(t.investment).toMatchObject({ state: 'ready', basis: 'Net present value at the WACC, after tax' })
    expect(t.investment.value).toMatch(/1\.20? M/)
    t = byId({ ...NONE, investment: { study: done, report: report(null, -11.01e6) } })
    expect(t.investment).toMatchObject({ state: 'ready',
      basis: 'Net present value before tax (no tax pack, so the after-tax figure is not established)' })
    t = byId({ ...NONE, investment: { study: done, report: report(null, null) } })
    expect(t.investment.state).toBe('unavailable')
  })

  it('investment: running, stale, refused', () => {
    expect(byId({ ...NONE, investment: { study: { status: 'running' } as never, report: null } })
      .investment.state).toBe('running')
    const stale = byId({ ...NONE, investment: { study: { status: 'done', stale: true } as never,
      report: { npv_at_wacc: 5e6 } as never } })
    expect(stale.investment.state).toBe('stale')
    const refused = byId({ ...NONE, investment: { study: { status: 'refused',
      message: 'no owner assets' } as never, report: null } })
    expect(refused.investment).toMatchObject({ state: 'unavailable' })
    expect(refused.investment.reason).toMatch(/no owner assets/)
    // a money-year refusal (smoke 2026-10-16, issue 03) reaches the tile whole: both years and the way out
    const money = byId({ ...NONE, investment: { study: { status: 'refused', error_code: 'campus_assets_money_year_mismatch',
      error: "campus_assets_money_year_mismatch: the campus assets are priced in 2026 money (the campus asset library's "
        + "price_year), the case in 2020 money (the finance inputs' currency_year); no price index converts between "
        + 'them, so a case with the campus assets needs the asset library and the finance inputs in one money year: '
        + "reprice the campus asset library in 2020, or state the finance inputs in the library's money year" } as never,
      report: null } })
    expect(money.investment.reason).toMatch(/2026 money .* 2020 money .* reprice the campus asset library in 2020/)
  })

  it('grid plan: the campus checks that pass with the proposed measures', () => {
    const row = (s: string) => ({ status_with_measures: s })
    const campus = (rows: { status_with_measures: string }[], stale = false) =>
      ({ results: { compliance: rows }, stale }) as never
    let t = byId({ ...NONE, grid: { campus: campus([row('pass'), row('pass'), row('fail')]), notHub: false } })
    expect(t.grid_plan).toMatchObject({ state: 'ready', value: '2 of 3', unit: 'checks pass',
      basis: 'Campus grid checks, with the proposed measures' })
    t = byId({ ...NONE, grid: { campus: campus([row('pass')], true), notHub: false } })
    expect(t.grid_plan.state).toBe('stale')
    t = byId({ ...NONE, grid: { campus: { results: null, stale: false } as never, notHub: false } })
    expect(t.grid_plan.state).toBe('not_run')
    t = byId({ ...NONE, grid: { campus: null, notHub: true } })
    expect(t.grid_plan.state).toBe('not_applicable')
  })

  it('operation: the dispatch solve, separate from the reliability study (G5)', () => {
    let t = byId({ ...NONE, operation: { status: { running: false, dispatch: 'none' } as never } })
    expect(t.operation.state).toBe('not_run')
    expect(t.operation.reason).toMatch(/reliability study does not solve it/)
    t = byId({ ...NONE, operation: { status: { running: true } as never } })
    expect(t.operation.state).toBe('running')
    t = byId({ ...NONE, operation: { status: { running: false, dispatch: 'fresh', objective: 3.2e6,
      condition: 'optimal', solve_time: 1 } as never } })
    // Not the cost again (UX audit M9): the Results cost tile already shows it.
    expect(t.operation).toMatchObject({ state: 'ready', value: 'Solved',
      basis: 'Every modelled hour dispatched; its cost is in the yearly cost above' })
    t = byId({ ...NONE, operation: { status: { running: false, dispatch: 'stale', objective: 3.2e6 } as never } })
    expect(t.operation.state).toBe('stale')
  })

  // GFM-b (owner decision 2026-10-15): the operation tile carries the control
  // line: the grid-forming settings' state, and the re-solve an apply needs.
  describe('the control line', () => {
    const rec = (over: Record<string, unknown> = {}) => ({
      label: 'heuristic screening', ledger: [], status: 'recommended', achievable: true, changed: true,
      settings: { BESS1: { model: 'gfm_droop', current: { droop_pct: 4, i_max_pu: 1.2, H_equivalent_s: 0.4 } },
                  BESS2: { model: 'gfm_vsm', current: { droop_pct: 4, i_max_pu: 1.2, H_equivalent_s: 0.4 } } },
      cases: [], capacity: [], comparison: [], simulations: 10, max_simulations: 400, margin: 0.1,
      allow_i_max: false, notes: [], excluded: [], ...over }) as never
    const ctl = (gfm: unknown) => byId({ ...NONE, operation: { status: null, gfm: gfm as never } }).operation.control

    it('none without a GFM read (no campus, no island)', () => {
      expect(ctl(null)).toBeUndefined()
    })
    it('not run, recommended, not achievable, stale', () => {
      expect(ctl({ state: 'not_run', reason: '', recommendation: null })).toMatchObject(
        { state: 'not_run', text: expect.stringMatching(/not recommended for this campus run/) })
      expect(ctl({ state: 'ready', reason: '', recommendation: rec() })).toMatchObject(
        { state: 'ready', text: expect.stringMatching(/recommended for 2 units, not applied yet/) })
      expect(ctl({ state: 'ready', reason: '', recommendation: rec({ status: 'not_achievable' }) })).toMatchObject(
        { state: 'ready', text: expect.stringMatching(/no setting passes: capacity is needed/) })
      expect(ctl({ state: 'stale', reason: 'x', recommendation: rec() })).toMatchObject(
        { state: 'stale', text: expect.stringMatching(/recommend them again/) })
    })
    it('applied: re-solve the hub, with the Solve action', () => {
      const applied = { at: 1, changes: [], hub_resolve_needed: true }
      const c = ctl({ state: 'stale', reason: 'x', recommendation: rec(), applied })
      expect(c).toMatchObject({ state: 'stale', resolve: true,
                                text: expect.stringMatching(/Re-solve the hub: these settings change its island rows/) })
      // a campus run removed the recommendation; the record still asks (#310 review)
      expect(ctl({ state: 'not_run', reason: '', recommendation: null, applied })).toMatchObject({ resolve: true })
      // island constraints off: the hub solve does not read them, no Solve
      expect(ctl({ state: 'stale', reason: '', recommendation: null,
                   applied: { ...applied, hub_resolve_needed: false } }))
        .toMatchObject({ state: 'ready', text: expect.stringMatching(/does not read them/) })
    })
  })
})


describe('chapterTiles: the campus run in progress', () => {
  const job = (over: Record<string, unknown>) => ({
    id: 'j1', kind: 'campus', status: 'running', position: null, error: null,
    enqueued_at: 100, started_at: 100, finished_at: null, stage: 'island_checks', stage_done: 2, stage_total: 5, ...over,
  })
  const grid = (run_job: unknown) => byId({ ...NONE, grid: { campus: { run_job } as never, notHub: false } }).grid_plan

  it('running: the stage, the step and the elapsed time', () => {
    const t = grid(job({ started_at: Date.now() / 1000 - 125 }))
    expect(t.state).toBe('running')
    expect(t.reason).toMatch(/island checks.*frequency sweep at the island hours/)
    expect(t.reason).toMatch(/step 3 of 5/)
    expect(t.reason).toMatch(/2:0\d\./)
  })

  it('queued: waiting in the queue', () => {
    expect(grid(job({ status: 'queued', position: 2, stage: null })).reason).toMatch(/solve queue \(#2\)/)
  })

  it('failed: the failed stage, the time it ran and the error, not "not run"', () => {
    const t = grid(job({ status: 'failed', finished_at: 400, error: 'ValueError: no PCC' }))
    expect(t.state).toBe('unavailable')
    expect(t.reason).toMatch(/failed in the island checks.* after 5:00: ValueError: no PCC/)
  })

  it('a grid-forming settings job is not a study run', () => {
    expect(grid(job({ stage: 'gfm_settings' })).state).toBe('not_run')
  })
})
