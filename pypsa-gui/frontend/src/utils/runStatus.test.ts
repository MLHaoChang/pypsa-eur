import { describe, expect, it } from 'vitest'
import type { SolveJob } from '../api/solveQueue'
import type { EhStudyPayload } from '../api/simulation'
import { aboutMinutes, clock, etaText, runFromJob, runFromStudy, runStatusText } from './runStatus'

const job = (over: Partial<SolveJob> = {}): SolveJob => ({
  id: 'j1', project_id: 'P', project_key: 'o:p', kind: 'solve', status: 'running', position: null,
  objective: null, solve_time: null, condition: null, error: null,
  enqueued_at: 100, started_at: 110, finished_at: null, ...over,
} as SolveJob)

const est = (seconds: number | null, samples = 3) => ({ kind: 'solve', seconds, samples, run_ids: [] })

describe('runFromJob', () => {
  it('names each kind in plain words, never the kind string', () => {
    expect(runFromJob(job({ kind: 'solve' })).name).toBe('Optimisation')
    expect(runFromJob(job({ kind: 'campus' })).name).toBe('Campus study')
    expect(runFromJob(job({ kind: 'gridspine' })).name).toBe('Grid study')
    expect(runFromJob(job({ kind: 'profile_gen' })).name).toBe('Generating profiles')
  })
  it('a queued job shows its place and no start', () => {
    const r = runFromJob(job({ status: 'queued', position: 2, started_at: null }))
    expect(r.stage).toBe('Waiting in queue #2')
    expect(r.startedAt).toBeNull()
    expect(r.queued).toBe(true)
  })
  it('a running solve is "Solving", other kinds "Running"', () => {
    expect(runFromJob(job()).stage).toBe('Solving')
    expect(runFromJob(job({ kind: 'campus' })).stage).toBe('Running')
    expect(runFromJob(job()).startedAt).toBe(110)
  })
})

describe('runFromStudy', () => {
  it('is null unless the study runs', () => {
    expect(runFromStudy(null)).toBeNull()
    expect(runFromStudy({ status: 'done' } as EhStudyPayload)).toBeNull()
  })
  it('counts steps against the upper bound', () => {
    const r = runFromStudy({
      status: 'running', started_at: 50, budget_solves: 30,
      report: { pipeline: { solves_consumed: 3, budget_solves: 30 } },
    } as unknown as EhStudyPayload)!
    expect(r.name).toBe('Reliability study')
    expect(r.steps).toBe('step 4 of up to 30')
    expect(r.jobId).toBeNull()
  })
  it('before the first step it names the bound only', () => {
    const r = runFromStudy({ status: 'running', budget_solves: 30 } as EhStudyPayload)!
    expect(r.steps).toBe('up to 30 steps')
  })
})

describe('etaText', () => {
  it('unknown says so, muted, never 0', () => {
    expect(etaText(est(null, 1), 10)).toEqual({ text: 'first runs: no estimate yet', muted: true, basis: '' })
  })
  it('no estimate at all shows nothing', () => {
    expect(etaText(undefined, 10)).toBeNull()
  })
  it('rounds the time left', () => {
    expect(etaText(est(400), 100)!.text).toBe('about 5 min left')
    expect(etaText(est(400), 350)!.text).toBe('under a minute left')
    expect(etaText(est(3600), 0)!.text).toBe('about 60 min left')
    expect(etaText(est(1000), 0)!.text).toBe('about 15 min left')
  })
  it('past the estimate it says longer than usual, never negative', () => {
    expect(etaText(est(360), 580)!.text).toBe('longer than usual (usually about 6 min)')
    expect(etaText(est(360), 360)!.text).toBe('longer than usual (usually about 6 min)')
  })
  it('names its basis', () => {
    expect(etaText(est(400, 2), 0)!.basis).toBe('Based on your last 2 runs of this project.')
  })
})

describe('formatting', () => {
  it('clock and minutes', () => {
    expect(clock(41)).toBe('0:41')
    expect(clock(188)).toBe('3:08')
    expect(aboutMinutes(59)).toBe('under a minute')
    expect(aboutMinutes(61)).toBe('1 min')
    expect(aboutMinutes(9 * 60)).toBe('9 min')
    expect(aboutMinutes(12 * 60)).toBe('10 min')
  })
  it('the full line joins the parts it has', () => {
    const r = runFromJob(job({ kind: 'campus' }))
    expect(runStatusText(r, 134, etaText(est(400), 134))).toBe('Campus study · Running · 2:14 · about 4 min left')
    expect(runStatusText(r, null, null)).toBe('Campus study · Running')
  })
})
