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
 * already terminal on the previous tick reports nothing. A job NOT in `prev`
 * at all DOES report (2026-08-14 review): polling stops while the queue is
 * idle, so a solve queued from another tab or the chat agent can appear for
 * the first time already `completed` — skipping it left the user reading
 * pre-solve results for a re-solved project. The don't-invalidate-all-history
 * property of the FIRST poll lives in the hook, which seeds its baseline
 * before ever diffing.
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
    if (before === job.status) continue
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
  // `null` = no baseline yet. The FIRST data seeds it and diffs nothing —
  // that is what keeps a page load from invalidating every project with a
  // finished job in the process-global listing. Every LATER poll diffs, and
  // `terminalTransitions` now counts a newly-appearing terminal id as a
  // transition (see its doc comment).
  const prevRef = useRef<Map<string, string> | null>(null)

  useEffect(() => {
    if (jobs === undefined) return
    const prev = prevRef.current
    prevRef.current = statusMap(jobs)
    if (prev === null) return
    for (const name of terminalTransitions(prev, jobs)) {
      qc.invalidateQueries({ queryKey: nk(name, 'results') })
      qc.invalidateQueries({ queryKey: nk(name, 'simulationStatus') })
      qc.invalidateQueries({ queryKey: nk(name, 'meta') })
      // The queue row's results preview caches under
      // `['resultsBundle', name, source]` with a 30 s staleTime — without
      // this key, expanding a re-solved row served the PREVIOUS solve's
      // numbers with no spinner (2026-08-14 review).
      qc.invalidateQueries({ queryKey: nk(name, 'resultsBundle') })
    }
  }, [jobs, qc])
}
