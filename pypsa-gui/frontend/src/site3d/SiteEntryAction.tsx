// The one "Create 3D site" action (UI contract rules 2 and 3). It renders the
// `/flow` scene entry verbatim (label, enabled, the blocker's reason and its
// one "›" home) and never derives availability itself. The only state it
// builds is "Save project first": an unsaved project has no `/flow`, so those
// two strings mirror `services/site_scene/verdicts.py` (pinned by a parity
// test) and the link points at the header's own Save, never a second button.
import { useProjectFlow } from '../hooks/useProjectFlow'
import { useUIStore } from '../store/uiStore'
import { openSiteAnchor, withReturnFrom } from '../layout/flowNavigation'
import type { SceneEntry } from '../api/flow'
import { useSiteCreation } from './siteCreation'

export const LABEL_CREATE = 'Create 3D site'
export const LABEL_SAVE_FIRST = 'Save project first'
export const LABEL_RESUME = 'Resume site setup'
export const LABEL_OPEN = 'Open 3D site'
export const REASON_SAVE_FIRST = 'Unsaved project: save it to keep a 3D site'

const SAVE_FIRST: SceneEntry = { label: LABEL_SAVE_FIRST, enabled: true, blocker: null, reason: REASON_SAVE_FIRST, target: { header_action: 'save' } }

/** Point at the header's Save: focus it and ring it briefly. Nothing is saved from here. */
export function pointAtHeaderSave(): void {
  const btn = document.querySelector<HTMLButtonElement>('button[aria-label="Save project"]')
  if (!btn) return
  btn.focus()
  btn.classList.add('ring-2', 'ring-accent')
  window.setTimeout(() => btn.classList.remove('ring-2', 'ring-accent'), 2000)
}

/** Open Site 3D on a part, remembering the view it left ("Back to Project & site"). */
export function openSite3D(part: 'map' | 'generate', creating = false): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.setFieldDrawer(null)
    ui.setSlidePanel(null)
    ui.setCanvasView('site')
    ui.setSitePart(part)
    useSiteCreation.getState().setOpen(creating)
  })
}

export function useSceneEntry(): { entry: SceneEntry | null; loading: boolean } {
  const project = useUIStore(s => s.currentProject)
  const { flow, isPending } = useProjectFlow()
  if (!project) return { entry: SAVE_FIRST, loading: false }
  return { entry: flow?.scene?.entry ?? null, loading: isPending }
}

export default function SiteEntryAction() {
  const { entry, loading } = useSceneEntry()
  if (loading) return <div className="text-[11px] text-muted">Checking the 3D site…</div>
  if (!entry) return null
  const saveFirst = entry.label === LABEL_SAVE_FIRST
  const run = () => {
    if (!entry.enabled || saveFirst) return
    if (entry.label === LABEL_CREATE) openSite3D('map', true)
    else openSite3D('generate')
  }
  return (
    <div data-testid="site-entry" className="flex flex-col gap-1">
      {saveFirst ? (
        // The label is the state, not a control: Save lives in the header.
        <span data-testid="site-entry-label" className="text-[12px] font-semibold text-text">{entry.label}</span>
      ) : (
        <button type="button" data-testid="site-entry-action" onClick={run} disabled={!entry.enabled}
          className="self-start px-3 py-1 rounded bg-accent text-white text-[12px] font-semibold hover:bg-accent/90 disabled:opacity-50">
          {entry.label}
        </button>
      )}
      {saveFirst && (
        <span data-testid="site-entry-reason" className="text-[11px] text-muted">
          {entry.reason}{' '}
          <button type="button" onClick={pointAtHeaderSave} className="text-accent hover:underline">› Save project</button>
        </span>
      )}
      {!entry.enabled && entry.blocker && (
        <span data-testid="site-entry-reason" className="text-[11px] text-muted">
          {entry.blocker.reason}{' '}
          {'guided' in entry.blocker.home && (
            <button type="button" onClick={() => openSiteAnchor()} className="text-accent hover:underline">
              {entry.blocker.link} Site coordinates
            </button>
          )}
        </span>
      )}
    </div>
  )
}
