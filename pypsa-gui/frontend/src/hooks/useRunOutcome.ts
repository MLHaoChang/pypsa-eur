import { useCallback, useEffect, useRef, useState } from 'react'
import { useUIStore } from '../store/uiStore'
import { useSolveQueue } from './useSolveQueue'
import { useHubStudy } from '../pages/hubDesign/useHubData'
import { isActive } from '../api/solveQueue'
import { outcomeFromJob, outcomeFromStudy, studyKey, type RunOutcome } from '../utils/runStatus'

/**
 * How the last run of the project on screen ended, kept until the user
 * dismisses it, a new run starts or the project changes (run progress slice
 * 2b; audit #25: the 6 s toast was the only way to reach the results).
 *
 * Only runs this tab saw going on count: a run that ended before the page
 * loaded is the "missed while away" summary's (slice 3), not this line's. A
 * finished run never jumps anywhere by itself (UX plan GO 1).
 */
export function useRunOutcome(): { outcome: RunOutcome | null; dismiss: () => void } {
  const project = useUIStore(s => s.currentProject)
  const { data: queue } = useSolveQueue()
  const { study } = useHubStudy()
  const seen = useRef(new Set<string>())
  const [outcome, setOutcome] = useState<RunOutcome | null>(null)

  useEffect(() => {
    seen.current = new Set()
    setOutcome(null)
  }, [project])

  useEffect(() => {
    if (!project) return
    let ended: RunOutcome | null = null
    let running = false
    for (const job of queue?.jobs ?? []) {
      if (job.project_id !== project) continue
      if (isActive(job)) { seen.current.add(job.id); running = true; continue }
      if (!seen.current.has(job.id)) continue
      seen.current.delete(job.id)
      ended = outcomeFromJob(job) ?? ended
    }
    const sKey = studyKey(study)
    if (sKey && study?.status === 'running') { seen.current.add(sKey); running = true }
    else if (sKey && seen.current.has(sKey)) {
      const o = outcomeFromStudy(study)
      if (o) { seen.current.delete(sKey); ended = o }
    }
    if (running) setOutcome(null)
    else if (ended) setOutcome(ended)
  }, [queue, study, project])

  const dismiss = useCallback(() => setOutcome(null), [])
  return { outcome, dismiss }
}
