import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openSectionInGuided, withReturnFrom } from '../../layout/flowNavigation'
import type { FlowTarget } from '../../api/flow'

/** Section 10, Results & reports: where every "Open results" lands. */
export const RESULTS_TARGET: FlowTarget = { section: 'results', panel: null, component: null, entity: null, field: null }

/**
 * "Open results" after a run (run progress slice 2b; UX plan GO 1). Guided
 * stays in Guided and shows the section-10 Results card (`hub-card-results`)
 * in place; Expert opens the Results section. Both go through `withReturn`,
 * so the header offers "Back" to where the user was.
 */
export function openResults(): void {
  if (useUIStore.getState().uiMode === 'guided') withReturnFrom(() => openSectionInGuided('results'))
  else openFlowTarget(RESULTS_TARGET)
}
