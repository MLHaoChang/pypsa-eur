/**
 * The Parameter guide's state (spec expert-catalogue §6.1): what it shows, the views
 * Back returns to, and the element focus goes back to on close. Its own store, not a
 * slice of `uiStore`: the guide is one drawer that any surface opens by id.
 *
 * Switching between Guided and Expert closes it (the drawer it sat in is gone).
 */
import { create } from 'zustand'
import { useUIStore } from '../store/uiStore'

export type BrowseTab = 'type' | 'group' | 'function'

export type GuideView =
  /** One entry, or an id still to resolve (an unknown one shows the search with a notice). */
  | { kind: 'entry'; id: string }
  /** The search field and the browse tabs; `item` is the opened asset type, group or function. */
  | { kind: 'home'; query: string; tab: BrowseTab; item: string | null; notice?: 'unknown' }

/** Views Back can return to. Older ones are dropped: Back is for retracing a few steps. */
export const HISTORY_LIMIT = 20

export const HOME: Extract<GuideView, { kind: 'home' }> = { kind: 'home', query: '', tab: 'type', item: null }

interface GuideState {
  /** null while closed. */
  view: GuideView | null
  history: GuideView[]
  /** Where focus returns on close: what had focus when the guide opened. */
  opener: HTMLElement | null
  /**
   * The field (model name) the guide was opened from, or null when unknown: the
   * palette row's entry, or the Properties field that had focus. Over that same
   * field the entry's "Open on" is hidden ("Back to …" already goes there).
   */
  originField: string | null
  /** Open an entry (or an asset type, group or function guide id); no id opens the search. */
  openGuide: (id?: string | null, opener?: HTMLElement | null, field?: string | null) => void
  /** Show another view; the current one goes onto the Back history. */
  showView: (view: GuideView) => void
  /** Change the current view in place (typing a query, switching a tab). */
  replaceView: (view: GuideView) => void
  back: () => void
  closeGuide: (opts?: { restoreFocus?: boolean }) => void
}

const push = (history: GuideView[], v: GuideView | null): GuideView[] =>
  (v ? [...history, v].slice(-HISTORY_LIMIT) : history)

/** The header's palette launcher (layout/AppHeader.tsx), found by its title (the header is snapshot-pinned). */
export const PALETTE_TRIGGER = 'header button[title^="Command palette"]'

/**
 * Where focus goes when the opener is gone (the palette's search field, a chat
 * message re-rendered away) or hidden: the palette launcher in the header. With
 * neither, focus stays where the browser leaves it (the page body).
 */
export function focusTargetAfterClose(opener: HTMLElement | null): HTMLElement | null {
  if (opener?.isConnected && !opener.closest('[hidden]')) return opener
  return document.querySelector<HTMLElement>(PALETTE_TRIGGER)
}

function restoreFocus(opener: HTMLElement | null): void {
  // After the close renders: a form the guide hid is shown again first.
  window.setTimeout(() => {
    focusTargetAfterClose(opener)?.focus()
  }, 0)
}

/** The Properties field (its `data-field` wrapper, layout/properties/AssetFields.tsx) an element sits in. */
const fieldOf = (el: HTMLElement | null): string | null =>
  el?.closest<HTMLElement>('[data-field]')?.dataset.field ?? null

export const useGuideStore = create<GuideState>((set, get) => ({
  view: null,
  history: [],
  opener: null,
  originField: null,
  openGuide: (id, opener, field) => {
    const view: GuideView = id ? { kind: 'entry', id } : HOME
    const s = get()
    if (s.view) { set({ view, history: push(s.history, s.view) }); return }
    const active = typeof document !== 'undefined' ? document.activeElement : null
    const from = opener ?? (active instanceof HTMLElement && active !== document.body ? active : null)
    set({ view, history: [], opener: from, originField: field ?? fieldOf(from) })
  },
  showView: view => set(s => ({ view, history: push(s.history, s.view) })),
  replaceView: view => set({ view }),
  back: () => set(s => (s.history.length
    ? { view: s.history[s.history.length - 1], history: s.history.slice(0, -1) }
    : {})),
  closeGuide: ({ restoreFocus: restore = true } = {}) => {
    const { view, opener } = get()
    if (!view) return
    set({ view: null, history: [], opener: null, originField: null })
    if (restore) restoreFocus(opener)
  },
}))

/** Shorthand for callers outside React (palette rows, chat links). */
export const openGuide = (id?: string | null, opener?: HTMLElement | null, field?: string | null): void =>
  useGuideStore.getState().openGuide(id, opener, field)

// A mode switch closes the guide: the drawer or panel it sat beside is replaced.
useUIStore.subscribe((s, prev) => {
  if (s.uiMode !== prev.uiMode) useGuideStore.getState().closeGuide({ restoreFocus: false })
})
