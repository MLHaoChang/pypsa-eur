// The link from the island panel (Expert › Grid study › Island) to an island
// unit's asset form, where its Controls block edits it, through the
// mode-aware openers so Back returns to the panel (control-strategy 02, UX 29
// condition 5).
import type { FlowTarget } from '../api/flow'
import { openFlowTarget, openFlowTargetInPlace } from '../layout/flowNavigation'
import { useUIStore } from '../store/uiStore'

/** The PyPSA classes an island unit can be, in the order a name is looked up. */
export const UNIT_CLASSES = ['StorageUnit', 'Generator', 'Store', 'Link'] as const
export type UnitClass = (typeof UNIT_CLASSES)[number]

/** The component class an island unit is, from the network's own lists (the sidecar keys
 *  units by PyPSA name), or null when no component has that name. */
export function unitClass(unit: string, lists: Partial<Record<UnitClass, { name: string }[]>>): UnitClass | null {
  return UNIT_CLASSES.find(c => (lists[c] ?? []).some(r => r.name === unit)) ?? null
}

/** The Back pill's words after the jump: the island panel is reached from Results in Guided and
 *  from Grid study in Expert, so the section's own name would not say where Back goes. */
export const ISLAND_RETURN_LABEL = 'Island'

/** Open the unit's asset form, at its Controls block: in place in Guided, the Properties panel in Expert. */
export function openAssetControls(type: UnitClass, name: string): void {
  const target: FlowTarget = { section: 'assets', panel: null, component: type, entity: name, field: null }
  const before = useUIStore.getState().flowReturn
  if (useUIStore.getState().uiMode === 'guided') openFlowTargetInPlace(target)
  else openFlowTarget(target)
  const back = useUIStore.getState().flowReturn
  if (back && back !== before) useUIStore.setState({ flowReturn: { ...back, label: ISLAND_RETURN_LABEL } })
}
