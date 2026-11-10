// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

import { useEffect } from 'react'
import { useUIStore, type SlidePanel } from '../store/uiStore'

/**
 * LC-14 item 5: scroll a panel to the section the assistant named.
 *
 * The request is one `{panel, section}` in the UI store. A panel passes `ready`
 * once its data has loaded (before that its sections are not on screen), then
 * this scrolls to `[data-panel-section="<id>"]` if it is rendered and clears
 * the request either way, so a section absent for this project (no campus
 * file yet, no results) leaves the panel at its top and never scrolls later.
 */
export function usePanelSectionScroll(panel: SlidePanel, ready: boolean): void {
  const request = useUIStore(s => s.panelSectionRequest)
  const clear = useUIStore(s => s.clearPanelSectionRequest)
  useEffect(() => {
    if (!request || request.panel !== panel || !ready) return
    document.querySelector(`[data-panel-section="${request.section}"]`)?.scrollIntoView?.({ block: 'start' })
    clear()
  }, [request, panel, ready, clear])
}
