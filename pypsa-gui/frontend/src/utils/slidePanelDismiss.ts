/**
 * Whether a mousedown outside the open slide panel should close it.
 *
 * Capture-phase in App.tsx. A click inside the panel, the sidebar, or any
 * element marked `data-no-panel-close` (the assistant dock and the floating
 * companion) keeps the panel. Detached nodes are ignored.
 */
export function slidePanelClickShouldClose(
  target: EventTarget | null,
  panel: HTMLElement | null,
): boolean {
  const el = target instanceof HTMLElement ? target : null
  if (!el || !document.body.contains(el)) return false
  if (panel?.contains(el)) return false
  if (el.closest('aside')) return false
  if (el.closest('[data-no-panel-close]')) return false
  return true
}
