import type { FlowTarget, FlowSectionId } from '../api/flow'
import { useUIStore, type SlidePanel } from '../store/uiStore'
import { SECTION_OPEN } from './flowSections'

// Showing a section or one flow item's field: the Guided cards, the Run's
// reason, and Issues all go through here.

/** Show a section in Expert: set it active, switch the mode, open its first
 *  panel. The switch keeps the section (workspace-reconciliation §7). */
export function openSectionInExpert(id: FlowSectionId): void {
  const ui = useUIStore.getState()
  ui.setActiveSection(id)
  ui.setUiMode('expert')
  const open = SECTION_OPEN[id]
  if (open.panel) ui.setSlidePanel(open.panel)
  else if (open.table) { ui.setSlidePanel(null); ui.requestBottomTab(open.table) }
}

/** Show a section's card in Guided: set it active and open the Guided hub, so
 *  the card is on screen (a closed panel would leave only the canvas). */
export function openSectionInGuided(id: FlowSectionId): void {
  const ui = useUIStore.getState()
  ui.setActiveSection(id)
  ui.setSlidePanel('hubDesign')
}

/** Show one flow item's field in Expert: its panel, or the component's own
 *  Properties. An item with no known field opens its section. */
export function openFlowTarget(target: FlowTarget): void {
  const ui = useUIStore.getState()
  if (target.panel) {
    ui.setActiveSection(target.section)
    ui.setUiMode('expert')
    ui.setSlidePanel(target.panel as SlidePanel)
    return
  }
  if (target.component && target.entity) {
    ui.setUiMode('expert')
    ui.setSelectedComponent({ type: target.component, name: target.entity })
    ui.setHighlightedComponent({ type: target.component, name: target.entity })
    ui.openRightPanel()
    ui.setSlidePanel(null)
    ui.setActiveSection(target.section)
    return
  }
  openSectionInExpert(target.section)
}

/** Open one flow item's field in place, in Guided: a drawer over the card with
 *  the component's own editor, or the panel it points at. An item with no known
 *  field opens its section in Expert instead (the one way to reach it). */
export function openFlowTargetInPlace(target: FlowTarget): void {
  const ui = useUIStore.getState()
  if (target.component && target.entity) {
    ui.setSelectedComponent({ type: target.component, name: target.entity })
    ui.setHighlightedComponent({ type: target.component, name: target.entity })
    ui.openRightPanel()
    ui.setFieldDrawer(target)
    // The item's own section, not the component class's: a Link on the site
    // fence is Connection & grid, not Assets.
    ui.setActiveSection(target.section)
    return
  }
  if (target.panel) { ui.setFieldDrawer(target); return }
  openSectionInExpert(target.section)
}
