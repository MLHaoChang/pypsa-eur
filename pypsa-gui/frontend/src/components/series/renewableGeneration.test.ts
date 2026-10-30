import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReadinessRow } from '../../api/profiles'
import { generateAndApply, generatePending, notReadyReason, readyRows, waitForJob } from './renewableGeneration'

const prof = vi.hoisted(() => ({ generate: vi.fn(), pending: vi.fn(), applyBatch: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, profilesApi: { ...real.profilesApi, ...prof } }
})

const row = (o: Partial<ReadinessRow>): ReadinessRow => ({
  generator: 'g_wind', technology: 'wind', bus: 'b', lon: 8, lat: 50, coord_source: 'bus', profile: 'none', ...o })

beforeEach(() => vi.clearAllMocks())

describe('readyRows', () => {
  const rows = [row({}), row({ generator: 'g_nc', coord_source: 'missing', lon: null, lat: null }),
    row({ generator: 'g_up', profile: 'uploaded' }), row({ generator: 'g_old', profile: 'generated' }),
    row({ generator: 'g_net', profile: 'network_native' })]
  it('keeps a measured or network series and a missing location out', () => {
    expect(readyRows(rows).map(r => r.generator)).toEqual(['g_wind'])
  })
  it('adds the earlier generated ones on request', () => {
    expect(readyRows(rows, true).map(r => r.generator)).toEqual(['g_wind', 'g_old'])
  })
})

describe('notReadyReason', () => {
  it('names each blocker', () => {
    expect(notReadyReason([])).toContain('no wind or solar generator')
    expect(notReadyReason([row({ profile: 'generated' })])).toContain('already generated')
  })
})

describe('generation', () => {
  it('keeps a tuned profile pending for comparison before any apply', async () => {
    prof.generate.mockResolvedValue({ id: 'j-tuned' })
    prof.pending.mockResolvedValue({ pending: [{ job_id: 'j-tuned' }], jobs: [], keep: 10 })
    await expect(generatePending('P', 'solar', ['g_pv'], { params: { losses: 0.1 } }))
      .resolves.toBe('j-tuned')
    expect(prof.generate).toHaveBeenCalledWith('P', {
      points: [], from_generators: ['g_pv'], technology: 'solar', params: { losses: 0.1 },
    })
    expect(prof.applyBatch).not.toHaveBeenCalled()
  })

  it('keeps one-click Generate on the project defaults without tuning params', async () => {
    prof.generate.mockResolvedValue({ id: 'j-default' })
    prof.pending.mockResolvedValue({ pending: [{ job_id: 'j-default' }], jobs: [], keep: 10 })
    prof.applyBatch.mockResolvedValue({ count: 1 })
    await expect(generateAndApply('P', [row({})], 0)).resolves.toEqual([
      { technology: 'wind', generators: ['g_wind'], applied: 1 },
    ])
    expect(prof.generate).toHaveBeenCalledWith('P', {
      points: [], from_generators: ['g_wind'], technology: 'wind',
    })
    expect(prof.applyBatch).toHaveBeenCalledWith('P', 'j-default')
  })

  it('does not offer a failed generation as a pending profile', async () => {
    prof.generate.mockResolvedValue({ id: 'j-failed' })
    prof.pending.mockResolvedValue({ pending: [], jobs: [], keep: 10 })
    await expect(generatePending('P', 'wind', ['g_wind'])).rejects.toThrow('did not finish')
    expect(prof.applyBatch).not.toHaveBeenCalled()
  })

  it('fails in words when the job leaves the queue without a result', async () => {
    prof.pending.mockResolvedValue({ pending: [], jobs: [], keep: 10 })
    await expect(waitForJob('P', 'j1', 0)).rejects.toThrow('did not finish')
  })

  it('reports a technology that failed and still does the other', async () => {
    prof.generate.mockImplementation(async (_p: string, s: { technology: string }) => {
      if (s.technology === 'wind') throw { response: { data: { detail: { message: 'cds_credential' } } } }
      return { id: 'j-solar' }
    })
    prof.pending.mockResolvedValue({ pending: [{ job_id: 'j-solar' }], jobs: [], keep: 10 })
    prof.applyBatch.mockResolvedValue({ applied: [{}], count: 1, skipped: [] })
    const out = await generateAndApply('P', [row({}), row({ generator: 'g_pv', technology: 'solar' })], 0)
    expect(out).toEqual([
      { technology: 'wind', generators: ['g_wind'], applied: 0, error: 'cds_credential' },
      { technology: 'solar', generators: ['g_pv'], applied: 1 },
    ])
  })
})
