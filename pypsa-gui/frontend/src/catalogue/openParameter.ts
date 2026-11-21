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
import { openSectionInGuided, withReturnFrom } from '../layout/flowNavigation'
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
  const ui = useUIStore.getState()
  // Both modes land the same way: the asset selected and its edit form asked for at the field.
  const land = () => {
    const s = useUIStore.getState()
    s.setSelectedComponent({ type, name: asset })
    s.setHighlightedComponent({ type, name: asset })
    s.openRightPanel()
    s.requestPropertiesEdit({ type, name: asset, field })
    focusWhenShown(field)
  }
  if (ui.uiMode === 'guided') {
    // Guided's home for a field: the drawer over its section's card (EXPERT-GUIDED-1).
    // Already on that card, the drawer is the way back; from anywhere else, Back returns.
    const section = sectionForComponent(type) ?? 'assets'
    const onCard = ui.activeSlidePanel === 'hubDesign' && ui.activeSection === section
    const jump = () => {
      if (!onCard) openSectionInGuided(section)
      useUIStore.getState().setFieldDrawer({ section, panel: null, component: type, entity: asset, field })
      land()
    }
    if (onCard) jump()
    else withReturnFrom(jump)
    return true
  }
  withReturnFrom(() => {
    useUIStore.getState().setSlidePanel(null)
    land()
  })
  return true
}

// ── Picking the asset ────────────────────────────────────────────────────────

let paletteSeed = ''

/** The query the palette opens on, once (then ''). */
export function takePaletteSeed(): string {
  const q = paletteSeed
  paletteSeed = ''
  return q
}

/** A parameter named without its asset: the palette opens on its plain name, so the user picks the asset. */
export function pickAssetFor(entry: Pick<CatalogueEntry, 'plain_name'>): void {
  paletteSeed = entry.plain_name
  useUIStore.getState().setPaletteMode('all')
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
