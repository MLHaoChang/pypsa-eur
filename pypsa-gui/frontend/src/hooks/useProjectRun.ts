import { useUIStore } from '../store/uiStore'
import { useSolveQueue, activeJobForProject } from './useSolveQueue'
import { useHubStudy } from '../pages/hubDesign/useHubData'
import { runFromJob, runFromStudy, type RunView } from '../utils/runStatus'

/**
 * The run of the project on screen, if one is queued or going on: its queue
 * job, else its Guided goal study (not a queue job). One source for the
 * header pill and the run strip, so Guided and Expert agree (run progress §1).
 */
export function useProjectRun(): RunView | null {
  const project = useUIStore(s => s.currentProject)
  const { data: queue } = useSolveQueue()
  const { study } = useHubStudy()
  const job = project ? activeJobForProject(queue, project) : undefined
  return (job ? runFromJob(job) : null) ?? runFromStudy(study)
}
