// When the assistant dock gets out of the way by itself (UX overhaul S5,
// owner decision Q5a, docs/superpowers/plans/2026-10-14-ux-overhaul.md §3
// "Density and spacing"). With Results open, or with no model key so the
// assistant cannot answer, the 420 px dock left the work area a sliver
// (finding W1). Unless the user pinned the dock, it collapses to its launcher
// while the condition holds and comes back when it ends.
//
// Layout and default state only: the dock's content and behaviour are the
// companion stream's (AssistantDock / ChatPanel). The collapse never persists,
// so the user's own stored open/closed choice is untouched, and a dock the
// user reopens by hand stays open until the condition next changes.
import { useEffect, useRef } from 'react'
import { useUIStore, type SlidePanel } from '../store/uiStore'
import { useChatReadiness } from '../hooks/useChatProfiles'

// Results only: Guided's Hub design is full width too, but its steps lean on
// the assistant ("Let the assistant do this"), so it keeps the dock.
const COLLAPSING_PANELS: ReadonlySet<SlidePanel> = new Set<SlidePanel>(['results'])

export function shouldAutoCollapse(
  panel: SlidePanel | null, keyReady: boolean | undefined, pinned: boolean,
): boolean {
  if (pinned) return false
  return (panel != null && COLLAPSING_PANELS.has(panel)) || keyReady === false
}

export interface DockAutoState { prevShould: boolean; autoCollapsed: boolean }
export const INITIAL_DOCK_AUTO: DockAutoState = { prevShould: false, autoCollapsed: false }

export function nextDockAuto(
  s: DockAutoState, { should, open }: { should: boolean; open: boolean },
): { state: DockAutoState; action: 'collapse' | 'restore' | null } {
  if (should && !s.prevShould) {
    return open
      ? { state: { prevShould: true, autoCollapsed: true }, action: 'collapse' }
      : { state: { prevShould: true, autoCollapsed: false }, action: null }
  }
  if (!should && s.prevShould) {
    return s.autoCollapsed && !open
      ? { state: INITIAL_DOCK_AUTO, action: 'restore' }
      : { state: INITIAL_DOCK_AUTO, action: null }
  }
  // Condition unchanged. A dock reopened by hand while auto-collapsed is the
  // user's choice now: forget the auto-collapse so nothing re-opens or
  // re-collapses it until the condition changes.
  if (s.autoCollapsed && open) return { state: { ...s, autoCollapsed: false }, action: null }
  return { state: s, action: null }
}

export function useDockAutoCollapse(): void {
  const panel = useUIStore(s => s.activeSlidePanel)
  const open = useUIStore(s => s.assistantDockOpen)
  const pinned = useUIStore(s => s.assistantDockPinned)
  const setOpenTransient = useUIStore(s => s.setAssistantDockOpenTransient)
  const { ready } = useChatReadiness()
  const state = useRef<DockAutoState>(INITIAL_DOCK_AUTO)
  const should = shouldAutoCollapse(panel, ready, pinned)
  useEffect(() => {
    const r = nextDockAuto(state.current, { should, open })
    state.current = r.state
    if (r.action === 'collapse') setOpenTransient(false)
    else if (r.action === 'restore') setOpenTransient(true)
  }, [should, open, setOpenTransient])
}
