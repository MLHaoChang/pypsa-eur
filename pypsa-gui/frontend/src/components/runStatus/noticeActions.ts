import { useUIStore } from '../../store/uiStore'
import { openResults } from './openResults'
import { useRunNotices, type RunNotice } from './runNotices'

/** Switch to the notice's project first when another one is on screen (spec §3). */
function showProject(n: RunNotice): void {
  const ui = useUIStore.getState()
  if (n.project && n.project !== ui.currentProject) ui.setCurrentProject(n.project)
}

/**
 * "Open results" from a notice: the same landing as the run strip's (#1630),
 * through `withReturn`, so Back names where the user was. The notice has done
 * its job and goes (UX 2 GO 3).
 */
export function openNoticeResults(n: RunNotice): void {
  showProject(n)
  openResults()
  useRunNotices.getState().dismiss(n.id)
}

/**
 * "See why" where a toast cannot show it in place: Expert opens the Solve
 * queue, whose row holds the error, as the run strip's See why does. Guided
 * shows the reason inside the notice instead (`RunNotices`).
 */
export function openNoticeWhy(): void {
  const ui = useUIStore.getState()
  if (ui.uiMode === 'expert') ui.setSlidePanel('solveQueue')
}
