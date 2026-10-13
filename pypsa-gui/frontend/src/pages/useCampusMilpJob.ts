// The campus joint optimisation (MILP) job, as the panel sees it (plan C12).
//
// The same shape as `pages/reports/useReportJob.ts`: `GET …/milp` is polled
// every 1.5 s while the record says `running` and not otherwise, and `start` /
// `cancel` are mutations over the job routes that re-read the status on
// success so polling begins on the next tick.
//
// The transition is what matters: on the poll where the record LEAVES
// `running` (→ `done` / `cancelled` / `failed`) `onFinished` is called once, so
// the panel re-reads its state and shows the results. A record already
// terminal at first read (the last job of an earlier visit) is a baseline.
import { useCallback, useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { campusApi, type CampusSettings, type MilpJobRecord } from '../api/campusElectrical'

export const CAMPUS_MILP_KEY = (project: string) => ['campusElectrical', 'milp', project] as const
export const CAMPUS_MILP_POLL_MS = 1500

export interface CampusMilpJobApi {
  record: MilpJobRecord | null
  isRunning: boolean
  isStarting: boolean
  /** A cancel was asked and the record has not left `running` yet. */
  cancelling: boolean
  start: (settings: CampusSettings) => Promise<MilpJobRecord>
  cancel: () => Promise<void>
}

export function useCampusMilpJob(
  project: string, options: { onStarted?: () => void; onFinished?: (r: MilpJobRecord) => void } = {},
): CampusMilpJobApi {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: CAMPUS_MILP_KEY(project),
    queryFn: () => campusApi.milpStatus(project),
    retry: false,
    refetchInterval: (q) => (q.state.data?.state === 'running' ? CAMPUS_MILP_POLL_MS : false),
  })
  const record = status.data ?? null
  const isRunning = record?.state === 'running'

  const prev = useRef<MilpJobRecord['state'] | null | undefined>(undefined)
  const onFinishedRef = useRef(options.onFinished)
  onFinishedRef.current = options.onFinished
  useEffect(() => {
    const now = record?.state ?? null
    const before = prev.current
    prev.current = now
    if (before !== 'running' || now === 'running' || !record) return
    onFinishedRef.current?.(record)
  }, [record])

  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: CAMPUS_MILP_KEY(project) }), [project, qc])

  const cancelMutation = useMutation({
    mutationFn: () => campusApi.cancelMilp(project),
    onSuccess: () => { void refresh() },
  })
  const startMutation = useMutation({
    mutationFn: (settings: CampusSettings) => campusApi.startMilp(project, settings),
    onSuccess: (rec) => {
      cancelMutation.reset()
      qc.setQueryData(CAMPUS_MILP_KEY(project), rec)
      options.onStarted?.()
      void refresh()
    },
  })

  return {
    record,
    isRunning,
    isStarting: startMutation.isPending,
    cancelling: isRunning && cancelMutation.isSuccess && cancelMutation.data?.cancelling === true,
    start: useCallback((s: CampusSettings) => startMutation.mutateAsync(s), [startMutation]),
    cancel: useCallback(async () => { await cancelMutation.mutateAsync() }, [cancelMutation]),
  }
}
