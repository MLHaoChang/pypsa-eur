// The two links between an island unit's Controls block (section 5, the
// asset form) and the island panel (Expert › Grid study › Island), each
// through the mode-aware openers so Back returns where the link was
// (control-strategy 02, UX 29 conditions 3 and 5).
import type { FlowTarget } from '../api/flow'
import { openFlowTarget, openFlowTargetInPlace, withReturnFrom } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'

/** The PyPSA classes an island unit can be, in the order a name is looked up. */
export const UNIT_CLASSES = ['StorageUnit', 'Generator', 'Store', 'Link'] as const
export type UnitClass = (typeof UNIT_CLASSES)[number]

/** The component class an island unit is, from the network's own lists (the sidecar keys
 *  units by PyPSA name), or null when no component has that name. */
export function unitClass(unit: string, lists: Partial<Record<UnitClass, { name: string }[]>>): UnitClass | null {
  return UNIT_CLASSES.find(c => (lists[c] ?? []).some(r => r.name === unit)) ?? null
}

/** Open the unit's asset form, at its Controls block: in place in Guided, the Properties panel in Expert. */
export function openAssetControls(type: UnitClass, name: string): void {
  const target: FlowTarget = { section: 'assets', panel: null, component: type, entity: name, field: null }
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
}

/** Open the island panel (where the project's control preset shows), scrolled to its Island section. */
export function openIslandPanel(from?: string): void {
  const ui = useUIStore.getState()
  if (ui.uiMode === 'guided') {
    openFlowTargetInPlace({ section: 'connection', panel: 'campusElectrical', component: null, entity: null, field: null })
  } else {
    withReturnFrom(() => ui.setSlidePanel('campusElectrical'), from)
  }
  useUIStore.getState().requestPanelSection('campusElectrical', 'island')
}
