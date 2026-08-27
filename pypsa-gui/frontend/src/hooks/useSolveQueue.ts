import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { solveQueueApi, isActive, type QueueList, type SolveJob } from '../api/solveQueue'

export const QUEUE_KEY = ['solveQueue'] as const

/**
 * Live view of the solve queue. Polls every 1.5s WHILE there is a queued or
 * running job, and stops polling when the queue is idle (refetchInterval → false)
 * to avoid a constant idle poll. Enqueue/abort mutations invalidate this key, so
 * a freshly-enqueued job restarts the polling cycle even from the idle state.
 *
 * Shared key — the panel and the per-tab badges both read it; React Query
 * dedupes to one request, and the panel's mount is what drives the interval.
 */
/** How often to re-probe a queue whose last fetch FAILED. Slow — this is a
 * "is the backend back yet" probe, not the active-solve poll. */
export const QUEUE_ERROR_RETRY_MS = 15_000

/**
 * Exported for tests. The error branch is load-bearing: returning `false`
 * whenever there was no data — including the ERROR state — combined with
 * `retry: 1` and the app-wide `refetchOnWindowFocus: false` left the queue
 * permanently dead after a backend blip during the first fetch (no interval,
 * no focus refetch, no retry affordance) until the panel was remounted. Same
 * failure class as the App.tsx SSE-probe bug documented in CLAUDE.md.
 */
export function queueRefetchInterval(
  query: { state: { status: string; data?: QueueList } },
): number | false {
  if (query.state.status === 'error') return QUEUE_ERROR_RETRY_MS
  const jobs = query.state.data?.jobs ?? []
  return jobs.some(isActive) ? 1500 : false
}

export function useSolveQueue() {
  return useQuery({
    queryKey: QUEUE_KEY,
    queryFn: solveQueueApi.list,
    refetchInterval: queueRefetchInterval,
    staleTime: 1000,
  })
}

export function useEnqueueSolve() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: string) => solveQueueApi.enqueue(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUEUE_KEY }),
  })
}

export function useAbortJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => solveQueueApi.abort(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUEUE_KEY }),
  })
}

export function useClearFinished() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => solveQueueApi.clearFinished(),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUEUE_KEY }),
  })
}

/** The active (queued or running) job for a project, if any — drives tab badges. */
export function activeJobForProject(list: QueueList | undefined, name: string): SolveJob | undefined {
  return list?.jobs.find(j => j.project_id === name && isActive(j))
}
