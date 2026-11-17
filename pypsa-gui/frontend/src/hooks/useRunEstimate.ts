import { useQuery } from '@tanstack/react-query'
import { runEstimateApi } from '../api/runEstimate'

/**
 * The estimate for one queue job. Read once per job: past runs do not change
 * while it runs, so there is no polling. Failure reads as "no estimate"
 * (the line then shows elapsed time only), never as an error on screen.
 */
export function useRunEstimate(jobId: string | null) {
  return useQuery({
    queryKey: ['runEstimate', jobId],
    queryFn: () => runEstimateApi.get(jobId!),
    enabled: !!jobId,
    staleTime: Infinity,
    retry: false,
  })
}
