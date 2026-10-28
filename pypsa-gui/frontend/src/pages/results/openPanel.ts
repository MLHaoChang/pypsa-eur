import { openFlowTarget, openFlowTargetInPlace } from '../../layout/flowNavigation'
import { useUIStore } from '../../store/uiStore'
import type { FlowTarget } from '../../api/flow'

/** Open a panel of a flow section, mode-aware (the shared navigation): in
 *  place in Guided, never flipping the mode; the section's panel in Expert. */
export function openPanel(section: FlowTarget['section'], panel: string): void {
  const target = { section, panel, component: null, entity: null, field: null }
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
}

/** Open one field of a component, mode-aware: a drawer over the card in Guided, the Properties panel in Expert. */
export function openComponentField(target: FlowTarget): void {
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
}
