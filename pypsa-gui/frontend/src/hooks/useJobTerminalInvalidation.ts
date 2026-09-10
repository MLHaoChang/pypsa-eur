import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { isTerminal, type SolveJob } from '../api/solveQueue'
import { nk } from '../utils/queryKeys'
import { useSolveQueue } from './useSolveQueue'

/**
 * Snapshot of `id -> status` for the current queue listing.
 *
 * Ids are stringified so the map keys stay stable when the backend's job id
 * becomes a UUID (increment 3) — the callers only ever compare them.
 */
export function statusMap(jobs: SolveJob[]): Map<string, string> {
  return new Map(jobs.map(j => [String(j.id), j.status]))
}

/**
 * Project names whose job just TRANSITIONED into a terminal status.
 *
 * "Transitioned" is what makes this safe to run on every 1.5 s poll: a job
 * already terminal on the previous tick reports nothing, and a job seen for the
 * first time reports nothing (otherwise the first poll after a page load would
 * invalidate every project with a finished job in the process-global listing).
 *
 * Deliberately no per-status branch. All four terminal statuses invalidate,
 * `interrupted` included — `isTerminal` is the single definition of the set.
 * A redacted row (`project_id: null`, a job the caller may not see) names no
 * project and is skipped rather than invalidating a cache keyed on `null`.
 */
export function terminalTransitions(
  prev: Map<string, string>,
  jobs: SolveJob[],
): string[] {
  const names: string[] = []
  for (const job of jobs) {
    const before = prev.get(String(job.id))
    if (before === undefined || before === job.status) continue
    if (!isTerminal(job)) continue
    if (!job.project_id) continue
    names.push(job.project_id)
  }
  return Array.from(new Set(names))
}

/**
 * Invalidate the React Query caches of each project whose job just finished.
 *
 * Replaces the `>0 → 0` resync effect in `ProjectTabs`, which reloaded the
 * CURRENT project from disk on any drain of the global active-job count —
 * discarding unsaved edits in a project that need never have been queued, and
 * firing on another organisation's redacted rows. Nothing is reloaded here:
 * increment 1 makes the solving context the same context the session holds, so
 * the fresh results are already in memory and only the caches are stale.
 */
export function useJobTerminalInvalidation(): void {
  const qc = useQueryClient()
  const { data } = useSolveQueue()
  const jobs = data?.jobs
  const prevRef = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    const list = jobs ?? []
    const finished = terminalTransitions(prevRef.current, list)
    prevRef.current = statusMap(list)
    for (const name of finished) {
      qc.invalidateQueries({ queryKey: nk(name, 'results') })
      qc.invalidateQueries({ queryKey: nk(name, 'simulationStatus') })
      qc.invalidateQueries({ queryKey: nk(name, 'meta') })
    }
  }, [jobs, qc])
}
