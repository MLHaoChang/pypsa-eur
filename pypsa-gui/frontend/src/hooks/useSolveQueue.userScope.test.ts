// The queue listing is per user: its rows carry the caller's own
// `started_by_me` and `can_dismiss` (run progress slice 3). A user switch must
// drop it, or the next account's first listing is the last one's cached rows,
// and its away summary would count and claim them (GK B3 (3) on #1716).
import { describe, expect, it } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { dropUserScopedQueries } from '../auth/AuthProvider'
import { QUEUE_KEY } from './useSolveQueue'

describe('the solve queue is user-scoped', () => {
  it("a user switch drops the last account's queue listing", () => {
    const qc = new QueryClient()
    qc.setQueryData(QUEUE_KEY, { jobs: [], running: [], paused: false })
    qc.setQueryData(['other-root'], 'kept')
    dropUserScopedQueries(qc)
    expect(qc.getQueryData(QUEUE_KEY)).toBeUndefined()
    expect(qc.getQueryData(['other-root'])).toBe('kept')
  })
})
