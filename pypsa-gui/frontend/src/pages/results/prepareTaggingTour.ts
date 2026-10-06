// Obstacle 3 (guided-mode spec §2.7): the `eh_tagging` tour's targets
// (`eh-bus-fields`, `eh-link-role`) render only in the Properties panel's
// Bus / Link card in Edit mode, but the tour is launched from Results, where
// the Properties panel is not rendered. This is the tour's `prepare` step:
// leave the full-screen panel, select a bus, open Properties and ask the Bus
// card for Edit — then give the card a moment to render the fields so the
// tour's first step finds its target.
import type { QueryClient } from '@tanstack/react-query'
import { networkApi } from '../../api/network'
import type { Bus } from '../../api/types'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

const FIRST_TARGET = 'eh-bus-fields'

/** Resolve once `[data-testid=id]` is in the DOM, or after `ms` (never rejects). */
function waitForTestId(id: string, ms: number): Promise<void> {
  return new Promise(resolve => {
    const found = () => document.querySelector(`[data-testid="${id}"]`) !== null
    if (ms <= 0 || found()) { resolve(); return }
    const t0 = Date.now()
    const tick = () => {
      if (found() || Date.now() - t0 >= ms) resolve()
      else setTimeout(tick, 50)
    }
    setTimeout(tick, 50)
  })
}

export async function prepareTaggingTour(
  qc: QueryClient,
  project: string | null,
  { waitMs = 3000 }: { waitMs?: number } = {},
): Promise<void> {
  // The buses query is usually cached (canvas, Properties); fetch if not.
  const buses = (qc.getQueryData<Bus[]>(nk(project, 'buses'))
    ?? await networkApi.getBuses()) as Bus[]
  const bus = buses.find(b => b.eh_poc === true) ?? buses[0]
  if (!bus) throw new Error('The network has no bus to tag yet.')
  const ui = useUIStore.getState()
  ui.setSlidePanel(null)
  ui.setSelectedComponent({ type: 'Bus', name: bus.name })
  ui.openRightPanel()
  ui.requestPropertiesEdit({ type: 'Bus', name: bus.name })
  await waitForTestId(FIRST_TARGET, waitMs)
}
