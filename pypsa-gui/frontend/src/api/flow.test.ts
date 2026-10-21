import { describe, it, expect, vi, beforeEach } from 'vitest'

const get = vi.fn()
const post = vi.fn()
vi.mock('./client', () => ({ client: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } }))

import { ackFlowItem, FLOW_SECTIONS, getProjectFlow } from './flow'
import { ALL_NETWORK_KEYS } from '../utils/projectActions'
import { COMPONENT_QUERY_ROOTS } from '../utils/assetWrite'

beforeEach(() => { get.mockReset(); post.mockReset() })

describe('flow client', () => {
  it('reads the flow of the named project', async () => {
    get.mockResolvedValue({ data: { sections: [], run_ready: true, run_blocker: null, notes: [], deferred: false, deferred_reason: null } })
    const body = await getProjectFlow('my site')
    expect(get).toHaveBeenCalledWith('/projects/my%20site/flow', { skipErrorToast: true })
    expect(body.run_ready).toBe(true)
  })

  it('acknowledges by code and entity and returns the stored ack', async () => {
    post.mockResolvedValue({ data: { ack: { section: 'commercial', code: 'assumed_default', entity: 'discount rate', value_hash: 'h', at: 't' } } })
    const ack = await ackFlowItem('p', 'assumed_default', 'discount rate')
    expect(post).toHaveBeenCalledWith('/projects/p/flow/ack', { code: 'assumed_default', entity: 'discount rate' })
    expect(ack.value_hash).toBe('h')
  })

  it('lists the ten sections in flow order', () => {
    expect(FLOW_SECTIONS).toEqual(['project', 'carriers', 'connection', 'loads', 'assets',
      'profiles', 'commercial', 'scenarios', 'readiness', 'results'])
  })

  it('is refreshed by every asset write and every network swap', () => {
    expect(COMPONENT_QUERY_ROOTS).toContain('flow')
    expect(ALL_NETWORK_KEYS).toContain('flow')
  })
})
