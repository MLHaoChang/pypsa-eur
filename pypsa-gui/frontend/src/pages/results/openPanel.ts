import { openFlowTarget } from '../../layout/flowNavigation'
import type { FlowTarget } from '../../api/flow'

/** Open a panel of a flow section, mode-aware (the shared navigation). */
export function openPanel(section: FlowTarget['section'], panel: string): void {
  openFlowTarget({ section, panel, component: null, entity: null, field: null })
}
