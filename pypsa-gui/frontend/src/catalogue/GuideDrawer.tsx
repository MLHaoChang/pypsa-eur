/**
 * The Parameter guide (spec expert-catalogue §6, WIKI-1): one right-hand drawer, built
 * from the catalogue, that explains a parameter and finds others by search or by
 * asset type, group and function.
 *
 * Where it sits (§6.1 "Stacking"; one right-hand drawer at a time):
 * - Guided with the field drawer open: inside that drawer, which keeps its form
 *   mounted and hidden, with "Back to <field>" (components/FieldDrawer.tsx). This
 *   component then renders nothing.
 * - Elsewhere: over the Properties panel's slot, the panel's width, left of the
 *   assistant dock, with "Back to <asset>" when an asset's Properties are showing.
 *   The panel underneath stays mounted, so unsaved edits survive.
 * - Narrow windows: when the guide and the open dock do not both fit beside some
 *   working area (`guideRightOffset`), the guide takes the dock's slot instead; the
 *   dock is untouched underneath and shows again when the guide closes.
 *
 * Non-modal (no focus trap). Esc closes the guide before anything else sees the key,
 * and only while the guide is open; focus returns to what opened it (guideStore).
 */
import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { ChevronLeft, X } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import type { Catalogue, CatalogueEntry } from './api'
import { useCatalogue } from './useCatalogue'
import { HOME, useGuideStore, type BrowseTab, type GuideView } from './guideStore'
import {
  browseEntries, browseItems, defaultText, placeOf, relatedEntries, resolveGuideId, splitGuide, typicalText, usedBy,
} from './guideModel'
import { buildParameterIndex } from './searchIndex'
import { MODEL_NAME_NOTE } from './parameterRows'
import { canOpenParameter, openParameter, pickAssetFor } from './openParameter'
import GuideMarkdown from './GuideMarkdown'

/** The Properties panel's width (layout/PropertiesPanel.tsx), so the guide takes exactly its slot. */
export const GUIDE_WIDTH = 300
/** The collapsed assistant rail (components/AssistantDock.tsx, `w-10`). */
export const DOCK_RAIL_WIDTH = 40
/** Room left of the guide for the canvas or the open view before the guide sits beside the dock. */
export const MIN_WORK_WIDTH = 320
export const UNKNOWN_NOTICE = 'That parameter is not in the list.'

/**
 * How far from the right edge the guide sits: left of the open dock when the guide,
 * the dock and a working area all fit; else over the dock's slot (0). Collapsed, the
 * dock's rail always stays uncovered.
 */
export function guideRightOffset(windowWidth: number, dockOpen: boolean, dockWidth: number): number {
  if (!dockOpen) return DOCK_RAIL_WIDTH
  return windowWidth - dockWidth >= GUIDE_WIDTH + MIN_WORK_WIDTH ? dockWidth : 0
}

const subscribeResize = (cb: () => void) => { window.addEventListener('resize', cb); return () => window.removeEventListener('resize', cb) }
const useWindowWidth = () => useSyncExternalStore(subscribeResize, () => window.innerWidth)

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Esc closes the guide first: a capture listener on window, bound only while the guide is open. */
function useGuideEscape(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // A modal on top (the palette, a confirm) closes first, by its own handler.
      if (useUIStore.getState().paletteMode !== null) return
      if ((e.target as Element | null)?.closest?.('[aria-modal="true"]')) return
      e.preventDefault()
      e.stopImmediatePropagation()
      useGuideStore.getState().closeGuide()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])
}

export interface GuideBack { label: string; onBack: () => void }

/** A focus key: changes when the user moves to another view, not on typing or a tab switch. */
const viewKey = (v: GuideView) => (v.kind === 'entry' ? `entry:${v.id}` : `home:${v.item ?? ''}:${v.notice ?? ''}`)

/** The guide's head and body, wherever it is hosted. Renders nothing while the guide is closed. */
export function GuidePanel({ backTo }: { backTo?: GuideBack }) {
  const view = useGuideStore(s => s.view)
  if (!view) return null
  return <OpenGuide view={view} backTo={backTo} />
}

function OpenGuide({ view, backTo }: { view: GuideView; backTo?: GuideBack }) {
  const history = useGuideStore(s => s.history)
  const back = useGuideStore(s => s.back)
  const close = useGuideStore(s => s.closeGuide)
  const replaceView = useGuideStore(s => s.replaceView)
  const catalogue = useCatalogue(true)
  const rootRef = useRef<HTMLDivElement>(null)
  const firstFocus = useRef(true)
  useGuideEscape()

  // An id that is not an entry: an asset type, group or function opens its list;
  // an unknown one opens the search with a notice.
  useEffect(() => {
    if (view.kind !== 'entry' || !catalogue) return
    const r = resolveGuideId(catalogue, view.id)
    if (!r) replaceView({ ...HOME, notice: 'unknown' })
    else if (r.kind === 'browse') replaceView({ ...HOME, tab: r.tab, item: r.item })
  }, [view, catalogue, replaceView])

  const key = viewKey(view)
  const resolved = view.kind === 'entry' && catalogue ? resolveGuideId(catalogue, view.id) : null
  const ready = view.kind === 'home' || resolved?.kind === 'entry'
  useEffect(() => {
    const root = rootRef.current
    if (!root || !ready) return
    const search = root.querySelector<HTMLElement>('[data-guide-search]')
    if (view.kind === 'home' && view.notice) search?.focus()
    else if (firstFocus.current) root.querySelector<HTMLElement>(FOCUSABLE)?.focus()
    else root.querySelector<HTMLElement>('[data-guide-title]')?.focus()
    firstFocus.current = false
  }, [key, ready])

  const prev = history[history.length - 1]
  const backControl: GuideBack | undefined = prev
    ? { label: `Back to ${viewName(prev, catalogue)}`, onBack: back }
    : backTo

  return (
    <div ref={rootRef} className="flex min-h-0 flex-1 flex-col" data-testid="guide-panel">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-2 px-3">
        {backControl && (
          <button type="button" onClick={backControl.onBack} data-testid="guide-back"
            className="flex min-w-0 items-center gap-0.5 truncate text-[11px] text-text hover:text-accent">
            <ChevronLeft size={12} className="shrink-0" aria-hidden="true" />
            <span className="truncate">{backControl.label}</span>
          </button>
        )}
        <span className="ml-auto shrink-0 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-muted">
          Parameter guide
        </span>
        <button type="button" aria-label="Close the guide" title="Close (Esc)" onClick={() => close()}
          data-testid="guide-close" className="shrink-0 text-muted hover:text-text"><X size={14} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-[11px] text-text">
        {!catalogue && <p className="text-muted">Loading the guide…</p>}
        {catalogue && view.kind === 'home' && <HomeView view={view} catalogue={catalogue} />}
        {catalogue && resolved?.kind === 'entry' && <EntryView entry={resolved.entry} catalogue={catalogue} hosted={backTo !== undefined} />}
      </div>
    </div>
  )
}

/** A view in words, for "Back to …". */
function viewName(v: GuideView, catalogue: Catalogue | undefined): string {
  if (v.kind === 'entry') {
    const r = catalogue ? resolveGuideId(catalogue, v.id) : null
    return r?.kind === 'entry' ? r.entry.plain_name : 'the guide'
  }
  if (v.query) return 'the results'
  if (v.item && catalogue) return browseItems(catalogue, v.tab).find(i => i.id === v.item)?.plain_name ?? 'the list'
  return 'the list'
}

// ── An entry ─────────────────────────────────────────────────────────────────

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_1fr] gap-2 py-1">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

function EntryView({ entry, catalogue, hosted }: { entry: CatalogueEntry; catalogue: Catalogue; hosted: boolean }) {
  const showView = useGuideStore(s => s.showView)
  const { meaning, example } = splitGuide(entry.guide)
  const { needed, also } = usedBy(entry, catalogue)
  const typical = typicalText(entry)
  const dflt = defaultText(entry)
  const related = relatedEntries(entry, catalogue)
  return (
    <article data-testid="guide-entry">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <h2 tabIndex={-1} data-guide-title className="text-[15px] font-semibold text-text outline-none">{entry.plain_name}</h2>
        <span className="text-[10px] text-muted">{placeOf(entry, catalogue)}</span>
      </div>
      {entry.short_help && <p className="mt-1 leading-relaxed">{entry.short_help}</p>}
      <dl className="mt-2 divide-y divide-border">
        {meaning && <Row label="What it means"><GuideMarkdown>{meaning}</GuideMarkdown></Row>}
        {needed.length > 0 && <Row label="Needed for">{needed.join(', ')}</Row>}
        {also.length > 0 && <Row label="Also used by">{also.join(', ')}</Row>}
        {typical && <Row label="Typical values">{typical}</Row>}
        {dflt && <Row label="Default">{dflt}</Row>}
        {related.length > 0 && (
          <Row label="Related">
            <span className="flex flex-wrap gap-x-2">
              {related.map(r => (
                <button key={r.id} type="button" onClick={() => showView({ kind: 'entry', id: r.id })}
                  className="text-text underline underline-offset-2 hover:text-accent">{r.plain_name} ›</button>
              ))}
            </span>
          </Row>
        )}
        {example && <Row label="Example"><GuideMarkdown>{example}</GuideMarkdown></Row>}
        <Row label="In model files">
          <span data-testid="guide-model-name" className="block truncate font-mono text-[10px] text-muted">{entry.model_name}</span>
        </Row>
      </dl>
      <OpenOn entry={entry} hosted={hosted} />
    </article>
  )
}

/**
 * "Open on <asset> ›" for the asset in hand when its class has the field; else "Choose an asset ›".
 * Hidden when the guide is hosted over that same field ("Back to …" already goes there):
 * Guided's field drawer for this very field, or Expert's Properties of this asset when
 * the guide was opened from this field. Another field of that asset keeps it (it scrolls
 * and flashes the field); an unknown origin keeps it too.
 */
function OpenOn({ entry, hosted }: { entry: CatalogueEntry; hosted: boolean }) {
  const uiMode = useUIStore(s => s.uiMode)
  const fieldDrawer = useUIStore(s => s.fieldDrawer)
  const selected = useUIStore(s => s.selectedComponent)
  const close = useGuideStore(s => s.closeGuide)
  const originField = useGuideStore(s => s.originField)
  if (!canOpenParameter(entry)) return null
  const inHand = uiMode === 'guided' && fieldDrawer?.component && fieldDrawer.entity
    ? { type: fieldDrawer.component, name: fieldDrawer.entity }
    : selected
  const asset = inHand && inHand.type === entry.scope ? inHand.name : null
  const sameAsBack = hosted && asset !== null
    && (uiMode === 'guided' ? fieldDrawer?.field : originField) === entry.model_name
  if (sameAsBack) return null
  const cls = 'mt-3 rounded border border-border px-2 py-1 text-[11px] text-text underline underline-offset-2 hover:bg-panel'
  return asset
    ? <button type="button" className={cls} data-testid="guide-open-on"
        onClick={() => { close({ restoreFocus: false }); openParameter({ entry, asset }) }}>Open on {asset} ›</button>
    : <button type="button" className={cls} data-testid="guide-choose-asset"
        onClick={() => { close({ restoreFocus: false }); pickAssetFor(entry) }}>Choose an asset ›</button>
}

// ── Search and browse ────────────────────────────────────────────────────────

const TABS: { id: BrowseTab; label: string }[] = [
  { id: 'type', label: 'Asset type' }, { id: 'group', label: 'Group' }, { id: 'function', label: 'Function' },
]

function EntryButton({ entry, catalogue, note }: { entry: CatalogueEntry; catalogue: Catalogue; note?: string }) {
  const showView = useGuideStore(s => s.showView)
  return (
    <li>
      <button type="button" onClick={() => showView({ kind: 'entry', id: entry.id })}
        className="w-full rounded px-1.5 py-1 text-left hover:bg-panel">
        <span className="block text-[11px] text-text">{entry.plain_name}</span>
        <span className="block truncate text-[10px] text-muted">{[placeOf(entry, catalogue), note].filter(Boolean).join(' · ')}</span>
      </button>
    </li>
  )
}

function HomeView({ view, catalogue }: { view: Extract<GuideView, { kind: 'home' }>; catalogue: Catalogue }) {
  const replaceView = useGuideStore(s => s.replaceView)
  const index = useMemo(() => buildParameterIndex(catalogue.entries), [catalogue])
  const hits = useMemo(() => (view.query.trim() ? index.search(view.query, { limit: 30 }) : []), [index, view.query])
  return (
    <div>
      <input type="search" data-guide-search value={view.query} aria-label="Search the parameter guide"
        placeholder="Find a parameter…"
        onChange={e => replaceView({ ...view, query: e.target.value, notice: undefined })}
        className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] text-text placeholder:text-muted focus:border-accent focus:outline-none" />
      <p role="status" className="mt-1 text-[11px] text-text empty:hidden">{view.notice === 'unknown' ? UNKNOWN_NOTICE : ''}</p>
      {view.query.trim()
        ? (hits.length
            ? <ul className="mt-2" aria-label="Results">{hits.map(h => (
                <EntryButton key={h.entry.id} entry={h.entry} catalogue={catalogue} note={h.via === 'model' ? MODEL_NAME_NOTE : undefined} />))}
              </ul>
            : <p className="mt-2 text-muted">No parameter matches “{view.query.trim()}”.</p>)
        : <Browse view={view} catalogue={catalogue} />}
    </div>
  )
}

function Browse({ view, catalogue }: { view: Extract<GuideView, { kind: 'home' }>; catalogue: Catalogue }) {
  const replaceView = useGuideStore(s => s.replaceView)
  const showView = useGuideStore(s => s.showView)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const select = (tab: BrowseTab) => { replaceView({ ...view, tab, item: null }); tabRefs.current[tab]?.focus() }
  const onTabKey = (e: React.KeyboardEvent) => {
    const i = TABS.findIndex(t => t.id === view.tab)
    const to = e.key === 'ArrowRight' ? (i + 1) % TABS.length
      : e.key === 'ArrowLeft' ? (i + TABS.length - 1) % TABS.length
      : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1
    if (to < 0) return
    e.preventDefault()
    select(TABS[to].id)
  }
  const items = browseItems(catalogue, view.tab)
  const item = view.item ? items.find(i => i.id === view.item) : undefined
  return (
    <div className="mt-2">
      <div role="tablist" aria-label="Browse by" className="flex gap-1 border-b border-border" onKeyDown={onTabKey}>
        {TABS.map(t => (
          <button key={t.id} type="button" role="tab" id={`guide-tab-${t.id}`} aria-controls="guide-tabpanel"
            aria-selected={view.tab === t.id} tabIndex={view.tab === t.id ? 0 : -1}
            ref={el => { tabRefs.current[t.id] = el }}
            onClick={() => select(t.id)}
            className={`-mb-px border-b-2 px-2 py-1 text-[11px] ${view.tab === t.id ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="guide-tabpanel" aria-labelledby={`guide-tab-${view.tab}`} className="pt-2">
        {item
          ? (
            <div>
              <h2 tabIndex={-1} data-guide-title className="text-[13px] font-semibold text-text outline-none">
                {view.tab === 'function' ? `What does ${item.plain_name.toLowerCase()} need?` : item.plain_name}
              </h2>
              {item.short_help && <p className="mt-0.5 text-muted">{item.short_help}</p>}
              {browseEntries(catalogue, view.tab, item.id).map(s => (
                <section key={s.title} className="mt-2">
                  <h3 className="text-[9px] font-semibold uppercase tracking-wide text-muted">{s.title}</h3>
                  <ul>{s.entries.map(e => <EntryButton key={e.id} entry={e} catalogue={catalogue} />)}</ul>
                </section>
              ))}
            </div>
          )
          : (
            <ul>
              {items.map(i => (
                <li key={i.id}>
                  <button type="button" onClick={() => showView({ ...view, item: i.id })}
                    className="w-full rounded px-1.5 py-1 text-left hover:bg-panel">
                    <span className="block text-[11px] text-text">{i.plain_name}</span>
                    {i.short_help && <span className="block truncate text-[10px] text-muted">{i.short_help}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  )
}

// ── The drawer ───────────────────────────────────────────────────────────────

/** Mounted once in App.tsx. Renders nothing while closed, or while the Guided field drawer hosts the guide. */
export default function GuideDrawer() {
  const open = useGuideStore(s => s.view !== null)
  const close = useGuideStore(s => s.closeGuide)
  const uiMode = useUIStore(s => s.uiMode)
  const fieldDrawer = useUIStore(s => s.fieldDrawer)
  const selected = useUIStore(s => s.selectedComponent)
  const activeSlidePanel = useUIStore(s => s.activeSlidePanel)
  const rightPanelOpen = useUIStore(s => s.rightPanelOpen)
  const dockOpen = useUIStore(s => s.assistantDockOpen)
  const dockWidth = useUIStore(s => s.assistantDockWidth)
  const windowWidth = useWindowWidth()
  if (!open || (uiMode === 'guided' && fieldDrawer)) return null
  // Over an asset's Properties panel: Back returns to it.
  const backTo = uiMode === 'expert' && selected && rightPanelOpen && !activeSlidePanel
    ? { label: `Back to ${selected.name}`, onBack: () => close() }
    : undefined
  return (
    <div role="dialog" aria-modal="false" aria-label="Parameter guide" data-testid="guide-drawer" data-no-panel-close
      className="fixed bottom-0 top-11 z-[60] flex max-w-full flex-col border-l border-border bg-bg shadow-xl"
      style={{ right: guideRightOffset(windowWidth, dockOpen, dockWidth), width: GUIDE_WIDTH }}>
      <GuidePanel backTo={backTo} />
    </div>
  )
}
