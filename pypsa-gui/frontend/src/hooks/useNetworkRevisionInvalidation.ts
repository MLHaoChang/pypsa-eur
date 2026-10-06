import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

/**
 * Re-read the hub study record and its review when the network is edited
 * (P33b 10b).
 *
 * After a finished hub study nothing polls `/results/eh_study` (it polls only
 * while `running`) or `/results/eh_review`, so an edit made after the study
 * left both cached as "not edited" until a reload. The backend's
 * `/network/undo/info` already carries the project's edit counter
 * (`network_revision`) and is polled every 3 s by three always-mounted
 * readers on the same key; this hook watches that sample and, when it moves,
 * invalidates the two hub queries so `edited_since_study` is re-read.
 *
 * The previous sample belongs to ONE project (the `useStudyFinishedInvalidation`
 * rule): the first sample, an `undefined` sample and a sample from another
 * project are never transitions. While `enabled` is false nothing fires and the
 * last sample is kept, so re-enabling (Expert → edit → Guided) with a moved
 * revision invalidates once. A single `prev` is enough: returning to a project
 * after another tab edited it is covered by react-query's refetch-on-mount.
 */
export function useNetworkRevisionInvalidation(enabled: boolean): void {
  const qc = useQueryClient()
  const project = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(project, 'undoInfo'),
    queryFn: () => networkApi.undoInfo(),
    refetchInterval: 3000,
    enabled: enabled && !!project,
  })
  const revision = typeof data?.network_revision === 'number' ? data.network_revision : undefined
  const prev = useRef<{ project: string | null; revision: number } | undefined>(undefined)
  useEffect(() => {
    if (!enabled || revision === undefined) return
    const before = prev.current
    prev.current = { project, revision }
    if (!before || before.project !== project) return
    if (before.revision !== revision) {
      void qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_study') })
      void qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_review') })
    }
  }, [enabled, revision, project, qc])
}
