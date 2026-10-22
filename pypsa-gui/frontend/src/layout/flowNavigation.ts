import type { FlowTarget, FlowSectionId } from '../api/flow'
import { useUIStore, type SlidePanel } from '../store/uiStore'
import { onManualNavigation } from '../utils/navigationEpoch'
import { fieldWords } from '../utils/flowItems'
import { PANEL_SECTION, SECTION_OPEN, sectionMeta } from './flowSections'

// Showing a section or one flow item's field: the Guided cards, the Run's
// reason, and Issues all go through here. Each jump remembers the view it left
// (`flowReturn`), so one "Back to <origin>" button brings the user back
// (design §2.2); the callers add nothing.

/** The view a fix link left, enough to put it back. */
export interface FlowReturn {
  label: string
  mode: 'guided' | 'expert'
  section: FlowSectionId | null
  panel: SlidePanel | null
  /** Guided: the drawer that was open (replaced by the jump), or null. */
  drawer: FlowTarget | null
  selected: { type: string; name: string } | null
  scroll: { path: string; top: number } | null
}

const cssPath = (el: Element): string => {
  const parts: string[] = []
  for (let n: Element | null = el; n && n !== document.body; n = n.parentElement) {
    const i = n.parentElement ? Array.prototype.indexOf.call(n.parentElement.children, n) + 1 : 1
    parts.unshift(`:nth-child(${i})`)
  }
  return 'body > ' + parts.join(' > ')
}

/** The scroll offset of the container holding the link the user clicked. */
function captureScroll(): FlowReturn['scroll'] {
  for (let n: Element | null = document.activeElement; n && n !== document.body; n = n.parentElement) {
    if (n.scrollTop > 0 && n.scrollHeight > n.clientHeight) return { path: cssPath(n), top: n.scrollTop }
  }
  return null
}

function restoreScroll(scroll: FlowReturn['scroll'], tries = 12): void {
  if (!scroll || typeof requestAnimationFrame === 'undefined') return
  requestAnimationFrame(() => {
    const el = document.querySelector(scroll.path)
    if (el && el.scrollHeight - el.clientHeight >= scroll.top) { el.scrollTop = scroll.top; return }
    if (tries > 0) restoreScroll(scroll, tries - 1)
  })
}

const sameTarget = (a: FlowTarget | null, b: FlowTarget): boolean =>
  !!a && a.section === b.section && a.panel === b.panel && a.component === b.component
    && a.entity === b.entity && a.field === b.field

/** The view the user is on now, or null when the jump stays in it. */
function currentOrigin(target: FlowTarget | null): FlowReturn | null {
  const ui = useUIStore.getState()
  const guided = ui.uiMode === 'guided'
  if (target && guided && sameTarget(ui.fieldDrawer, target)) return null
  if (target && !guided && target.panel && ui.activeSlidePanel === target.panel) return null
  const panel = ui.activeSlidePanel
  const section = guided
    ? (ui.fieldDrawer ? ui.fieldDrawer.section : ui.activeSection)
    : ((panel && PANEL_SECTION[panel]) || ui.activeSection)
  const where = guided && ui.fieldDrawer
    ? fieldWords(ui.fieldDrawer) ?? sectionMeta(ui.fieldDrawer.section).label
    : section ? sectionMeta(section).label : null
  return {
    label: where ?? (guided ? 'the cards' : 'the canvas'),
    mode: ui.uiMode, section: ui.activeSection, panel, drawer: ui.fieldDrawer,
    selected: ui.selectedComponent ? { type: ui.selectedComponent.type, name: ui.selectedComponent.name } : null,
    scroll: guided && !ui.fieldDrawer ? null : captureScroll(),
  }
}

/** Run a jump and remember where it started. The store's own navigation hooks
 *  clear a pending return, so it is set after the jump. */
function withReturn(target: FlowTarget | null, jump: () => void): void {
  const origin = currentOrigin(target)
  jump()
  if (origin) useUIStore.setState({ flowReturn: origin })
}

/** "Back to <origin>": put the view a fix link left back, with its scroll. */
export function returnToOrigin(): void {
  const o = useUIStore.getState().flowReturn
  if (!o) return
  const ui = useUIStore.getState()
  ui.setUiMode(o.mode)
  ui.setActiveSection(o.section)
  if (o.mode === 'guided') {
    ui.setSlidePanel('hubDesign')
    if (o.selected) ui.setSelectedComponent(o.selected as never)
    ui.setFieldDrawer(o.drawer)
  } else {
    ui.setSlidePanel(o.panel)
    if (!o.panel && o.selected) ui.setSelectedComponent(o.selected as never)
    ui.setFieldDrawer(null)
  }
  useUIStore.setState({ flowReturn: null })
  restoreScroll(o.scroll)
}

// Any other navigation makes the pending return stale.
onManualNavigation(() => {
  if (useUIStore.getState().flowReturn) useUIStore.setState({ flowReturn: null })
})

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

/** Time Series on one component (W3c's "Next: give it a time series ›"):
 *  its tab, the field's slot when given (p_set, p_max_pu, p_min_pu,
 *  marginal_cost), and that field's own editor when `openEditor` is set.
 *  Renewables and demand response have no editor; they open on their row. */
export function openTimeSeriesFor(
  type: 'Load' | 'Generator' | 'Link', name: string,
  opts: { field?: string; openEditor?: boolean } = {},
): void {
  const ui = useUIStore.getState()
  if (ui.uiMode === 'guided') {
    // In place over the card: the Time Series panel in the field drawer,
    // which reads the focus when it mounts.
    openFlowTargetInPlace({ section: 'profiles', panel: 'timeseries', component: null,
      entity: null, field: opts.field ?? null })
    useUIStore.setState({ timeSeriesFocus: { type, name, ...opts } })
    return
  }
  withReturn(null, () => { openSectionInExpert('profiles'); ui.focusTimeSeries(type, name, opts) })
}

/** Show the Economic assumptions block of Solver Settings (default lifetime,
 *  CO₂ price): the section's panel, on the Dispatch tab, scrolled to the block. */
export function openEconomicAssumptions(): void {
  useUIStore.getState().requestSolverBlock('economic')
  withReturn(null, () => openSectionInExpert('scenarios'))
}

/** Show the N-1 security branch selector (Solver settings › Network): the
 *  section's panel in Expert, in place in Guided (never flips the mode). */
export function openN1BranchSelector(): void {
  const ui = useUIStore.getState()
  ui.requestSolverBlock('sclopf')
  if (ui.uiMode === 'guided') {
    openFlowTargetInPlace({ section: 'scenarios', panel: 'simparams', component: null,
      entity: null, field: null })
    return
  }
  withReturn(null, () => openSectionInExpert('scenarios'))
}

/** Show one flow item's field in Expert: its panel, or the component's own
 *  Properties. An item with no known field opens its section. */
export function openFlowTarget(target: FlowTarget): void {
  withReturn(target, () => openFlowTargetNow(target))
}

function openFlowTargetNow(target: FlowTarget): void {
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
  withReturn(target, () => openFlowTargetInPlaceNow(target))
}

function openFlowTargetInPlaceNow(target: FlowTarget): void {
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
