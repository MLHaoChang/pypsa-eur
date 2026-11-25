import { useResultsOutOfDateFlag } from '../store/resultsOutOfDate'
import { useUIStore } from '../store/uiStore'
import { useStaleRunKinds } from './useStaleRunKinds'

/** Whether the open project's results are withheld because its inputs changed after the last run. */
export function useResultsOutOfDate(): boolean {
  const project = useUIStore(s => s.currentProject)
  const flagged = useResultsOutOfDateFlag(s => s.project)
  const stale = useStaleRunKinds().includes('Optimisation')
  return !!project && flagged === project && stale
}
