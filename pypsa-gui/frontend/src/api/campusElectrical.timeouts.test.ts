// A study run is one synchronous request that takes seconds to minutes (18 to
// 31 s on a realistic campus at 3 hours per criterion, more with more hours),
// and starting the joint optimisation makes that run first unless a fresh one
// exists. The client's 30 s default would abandon both while the backend
// finishes and saves; drafting loads the whole saved network. Each such call
// carries its own timeout, as the bundle download and import do
// (`projects.ts`). A refusal or a request with no answer is told apart.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from './client'
import { CAMPUS_DRAFT_TIMEOUT_MS, CAMPUS_RUN_TIMEOUT_MS, campusApi, isNoAnswer } from './campusElectrical'

const get = vi.spyOn(client, 'get')
const post = vi.spyOn(client, 'post')
const put = vi.spyOn(client, 'put')
const SETTINGS = {
  k: 3, pf: 0.95, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true, invest: true,
  pcc_switchgear_by_operator: false, pcc_tap_control: 'campus' as const,
}

beforeEach(() => {
  for (const m of [get, post, put]) m.mockResolvedValue({ data: {} } as never)
})
afterEach(() => vi.clearAllMocks())

describe('campusApi: the long calls carry their own timeout', () => {
  it('queues the study with an ordinary request: it answers with the job, not the results', async () => {
    await campusApi.run('Hub A', SETTINGS)
    const [url, body, cfg] = post.mock.calls[0]
    expect(url).toBe('/campus-electrical/Hub%20A/run')
    expect(body).toEqual(SETTINGS)
    expect(cfg).toEqual(expect.objectContaining({ skipErrorToast: true }))
    expect(cfg?.timeout).toBeUndefined()
    expect(CAMPUS_RUN_TIMEOUT_MS).toBeGreaterThanOrEqual(10 * 60_000)      // the MILP start still makes a run
  })

  it('starts the joint optimisation with the same timeout: it makes the least-cost run first', async () => {
    await campusApi.startMilp('Hub A', SETTINGS)
    expect(post.mock.calls[0][2]).toEqual(expect.objectContaining({ timeout: CAMPUS_RUN_TIMEOUT_MS }))
  })

  it('drafts with the bundle download timeout: it loads the whole saved network', async () => {
    await campusApi.draft('Hub A', true)
    expect(post.mock.calls[0][2]).toEqual(expect.objectContaining({ timeout: CAMPUS_DRAFT_TIMEOUT_MS }))
    expect(CAMPUS_DRAFT_TIMEOUT_MS).toBe(180_000)
  })

  it('leaves the quick calls on the default', async () => {
    await campusApi.state('Hub A')
    await campusApi.save('Hub A', 'campus: {}')
    expect(get.mock.calls[0][1]).not.toHaveProperty('timeout')
    expect(put.mock.calls[0][2]).not.toHaveProperty('timeout')
  })
})

describe('isNoAnswer', () => {
  it('is true for a timeout or a dropped connection, which leave no response', () => {
    expect(isNoAnswer({ code: 'ECONNABORTED', message: 'timeout of 30000ms exceeded' })).toBe(true)
    expect(isNoAnswer({ code: 'ETIMEDOUT', message: 'timeout' })).toBe(true)
    expect(isNoAnswer({ code: 'ERR_NETWORK', message: 'Network Error' })).toBe(true)
  })

  it('is false for a refusal the backend answered, and for anything else', () => {
    expect(isNoAnswer({ code: 'ERR_BAD_REQUEST', response: { status: 422, data: { detail: 'bad' } } })).toBe(false)
    expect(isNoAnswer({ code: 'ECONNABORTED', response: { status: 504 } })).toBe(false)
    expect(isNoAnswer(new Error('boom'))).toBe(false)
    expect(isNoAnswer(null)).toBe(false)
  })
})
