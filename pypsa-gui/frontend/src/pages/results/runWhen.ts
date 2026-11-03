import { useQuery } from '@tanstack/react-query'
import { resultsEvidenceApi } from '../../api/resultsEvidence'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

/** A run as the user knows it: when it started, "31 Oct, 14:05" (the run id is
 *  a hash, so it goes in a tooltip). `seconds` tells apart two runs of one
 *  minute. Null when the run recorded no start time. */
export function runWhen(startedAt: string | null | undefined, seconds = false): string | null {
  if (!startedAt) return null
  const at = new Date(startedAt)
  if (Number.isNaN(at.getTime())) return null
  const day = at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  const time = at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) })
  return `${day}, ${time}`
}

/** "31 Oct, 14:05" for two runs, with seconds when the minutes alone match. */
export function runWhenPair(a: string | null | undefined, b: string | null | undefined): [string | null, string | null] {
  const [x, y] = [runWhen(a), runWhen(b)]
  return x != null && x === y ? [runWhen(a, true), runWhen(b, true)] : [x, y]
}

/** A short fallback name when no start time is known. */
export const runShort = (runId: string): string => `run ${runId.slice(0, 8)}`

/** The start time of a run the Results page shows from kept evidence: the
 *  run view's own summary read (same query key, so no second request). */
export function useRunStartedAt(runId: string | undefined): string | null {
  const project = useUIStore(s => s.currentProject)
  const query = useQuery({
    queryKey: nk(project, 'run-view', runId, undefined, undefined),
    queryFn: () => resultsEvidenceApi.run(runId!),
    enabled: !!project && !!runId,
    // A run's start time never changes: this observer never refetches the
    // shared read (RunBoundView's own observer rechecks access on each mount).
    staleTime: Infinity,
  })
  return query.data?.binding.run_id === runId ? query.data?.data.started_at ?? null : null
}
