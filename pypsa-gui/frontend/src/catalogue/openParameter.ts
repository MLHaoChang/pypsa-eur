/**
 * One jump to one parameter (spec expert-catalogue §4.3), used by the ⌘K palette,
 * chat navigation (`parameter` kind) and, later, the guide and the "needs" markers.
 *
 * Expert: the asset's Properties panel, with the field scrolled to, focused and
 * flashed (`requestPropertiesEdit` → `focusFieldControl`). Guided: the same
 * fields in the field drawer over the current card (EXPERT-GUIDED-1); Guided
 * never switches mode on its own. Both go through `withReturn`, so the Back
 * pill returns to where the user was.
 */
import { useUIStore, type PropertiesEditRequest } from '../store/uiStore'
import { openFlowTargetInPlace, withReturnFrom } from '../layout/flowNavigation'
import { sectionForComponent } from '../layout/flowSections'
import { focusFieldControl } from '../layout/properties/AssetFields'
import { navigationEpoch } from '../utils/navigationEpoch'
import { fetchCatalogue, type Catalogue, type CatalogueEntry } from './api'

/** The classes whose Properties cards take a field request. */
const EDITABLE: ReadonlySet<string> = new Set<PropertiesEditRequest['type']>(
  ['Bus', 'Line', 'Transformer', 'Link', 'Generator', 'StorageUnit', 'Store', 'Load'])

export interface ParameterTarget {
  entry: Pick<CatalogueEntry, 'scope' | 'model_name'>
  /** The asset to open it on (its name); the entry's own class. */
  asset: string
}

/** Whether `openParameter` can land on this entry's field (its class has a Properties card). */
export const canOpenParameter = (entry: Pick<CatalogueEntry, 'scope'>): boolean => EDITABLE.has(entry.scope)

/** How long a jump waits for the edit form to show the field. */
export const FOCUS_WAIT_MS = 3000
const FOCUS_POLL_MS = 100

/**
 * Request-then-scroll: the card opens its edit form on the request; once the field's
 * control is in the page, scroll to it, focus and flash it. Gives up after
 * FOCUS_WAIT_MS, or as soon as the user navigates elsewhere.
 */
function focusWhenShown(field: string): void {
  const epoch = navigationEpoch()
  const started = Date.now()
  const tick = () => {
    if (navigationEpoch() !== epoch || Date.now() - started > FOCUS_WAIT_MS) return
    if (!focusFieldControl(field)) window.setTimeout(tick, FOCUS_POLL_MS)
  }
  window.setTimeout(tick, 0)
}

/** Open one asset's field. Returns false, moving nothing, for a class with no Properties card. */
export function openParameter({ entry, asset }: ParameterTarget): boolean {
  if (!canOpenParameter(entry) || !asset) return false
  const type = entry.scope as PropertiesEditRequest['type']
  const field = entry.model_name
  const request = () => {
    useUIStore.getState().requestPropertiesEdit({ type, name: asset, field })
    focusWhenShown(field)
  }
  if (useUIStore.getState().uiMode === 'guided') {
    const section = sectionForComponent(type) ?? 'assets'
    openFlowTargetInPlace({ section, panel: null, component: type, entity: asset, field })
    request()
    return true
  }
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.setSlidePanel(null)
    ui.setSelectedComponent({ type, name: asset })
    ui.setHighlightedComponent({ type, name: asset })
    ui.openRightPanel()
    request()
  })
  return true
}

/** The entry with this id, or the one that lists it among its `former_ids`; null when neither. */
export function findEntry(catalogue: Pick<Catalogue, 'entries'>, id: string): CatalogueEntry | null {
  return catalogue.entries.find(e => e.id === id)
    ?? catalogue.entries.find(e => e.former_ids.includes(id))
    ?? null
}

/** The catalogue entry for an id (current or former), fetched when not yet held. */
export async function resolveEntry(id: string): Promise<CatalogueEntry | null> {
  return findEntry(await fetchCatalogue(), id)
}
