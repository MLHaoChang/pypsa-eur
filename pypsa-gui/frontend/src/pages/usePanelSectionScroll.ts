// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react'
import { useUIStore, type SlidePanel } from '../store/uiStore'

/** Panels currently mounted, by id. StrictMode unmounts and remounts in one commit; this tells that from a real leave. */
const mounted = new Map<SlidePanel, number>()

/**
 * LC-14 item 5: scroll a panel to the section the assistant named.
 *
 * The request is one `{panel, section}` in the UI store (a second request replaces the first).
 * A panel passes `ready` once its data has loaded (before that its sections are not on screen),
 * then this scrolls to `[data-panel-section="<id>"]` if it is rendered and clears the request
 * either way, so a section absent for this project (no campus file yet, no results) leaves the
 * panel at its top and never scrolls later. A request whose data never arrives is cleared when
 * the panel goes away, so it cannot scroll a later visit.
 */
export function usePanelSectionScroll(panel: SlidePanel, ready: boolean): void {
  const request = useUIStore(s => s.panelSectionRequest)
  const clear = useUIStore(s => s.clearPanelSectionRequest)
  const latest = useRef({ request, clear })
  latest.current = { request, clear }
  useEffect(() => {
    if (!request || request.panel !== panel || !ready) return
    document.querySelector(`[data-panel-section="${request.section}"]`)?.scrollIntoView?.({ block: 'start' })
    clear()
  }, [request, panel, ready, clear])
  useEffect(() => {
    mounted.set(panel, (mounted.get(panel) ?? 0) + 1)
    return () => {
      mounted.set(panel, (mounted.get(panel) ?? 1) - 1)
      queueMicrotask(() => {
        if ((mounted.get(panel) ?? 0) > 0) return
        if (latest.current.request?.panel === panel) latest.current.clear()
      })
    }
  }, [panel])
}
