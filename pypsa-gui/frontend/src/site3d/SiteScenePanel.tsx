// SC-01b: the native scene's controls inside Site 3D (top-left column).
// - The four parts as one row of labels (rule 5): no rail, card or section
//   completion; Model and Results say why they are not available here, from
//   the `/flow` scene actions, with one "›" link to their home.
// - Generate: mapped buildings inside the boundary plus proposed rectangles
//   go to a proposal; its preview is drawn as ghosts and reviewed, then
//   Apply commits it (a new scene revision) or Discard cancels it.
// - Correction: a selected accepted building gets numeric controls; "Preview
//   change" is an `edit_buildings` proposal reviewed the same way.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useProjectFlow } from '../hooks/useProjectFlow'
import { openSectionInExpert, openSectionInGuided, openSiteAnchor, withReturnFromSite3D } from '../layout/flowNavigation'
import { sceneErrorCopy, sceneErrorDetail, sceneErrorWantsReload, type GreenfieldInput, type SceneBuilding, type SourceInput } from '../api/siteScene'
import type { SceneAction, SitePart } from '../api/flow'
import { evaluateMutation } from '../utils/mutationGuard'
import NumberField from './NumberField'
import { useSitesStore } from './sitesStore'
import { applyProposal, discardProposal, propose, SitesNotSaved } from './sceneActions'
import { useSceneProposal, useSiteScene } from './useSiteScene'
import { heightEvidenceWords, HEIGHT_MAX_M, greenfieldFromBuilding, osmSourcesInside } from './nativeSite'
import { fromLocal } from './geo'
import type { ContextBuilding, Site } from './types'

const PARTS: { id: SitePart; label: string }[] = [
  { id: 'map', label: 'Map' }, { id: 'generate', label: 'Generate' }, { id: 'model', label: 'Model' }, { id: 'results', label: 'Results' },
]
const ADOPT_NOTE = 'Generating makes this site a 3D scene: its boundary and equipment positions are then managed by the scene'
const NO_SOURCES = 'No mapped buildings: refresh the site context or add buildings by hand'
const FIX_FIELDS = 'Correct the field marked in red to continue'
const n = (v: number) => v.toLocaleString('en-US')

/** The "›" home of a blocker that leaves Site 3D (rule 4), through withReturn. */
function goHome(home: unknown): void {
  const h = home as { guided?: { section: string }; expert?: unknown; site_part?: SitePart }
  if (h.site_part) { useUIStore.getState().setSitePart(h.site_part); return }
  if (!h.guided) return
  const section = h.guided.section as Parameters<typeof openSectionInGuided>[0]
  if (section === 'project') { openSiteAnchor({ fromSite3D: true }); return }
  withReturnFromSite3D(() => {
    if (useUIStore.getState().uiMode === 'guided') openSectionInGuided(section)
    else openSectionInExpert(section)
  })
}

const RESULTS_NOT_YET = 'Not available yet: 3D results come in a later version'

/** "› Snapshots" beside an unreadable scene (UX SC-01b plan, condition 3). */
function SnapshotsLink() {
  return (
    <button type="button" data-testid="scene-snapshots-link" className="text-accent hover:underline"
      onClick={() => withReturnFromSite3D(() => useUIStore.getState().setSlidePanel('snapshots'))}>
      › Snapshots
    </button>
  )
}

const unreadable = (err: unknown) => sceneErrorDetail(err)?.error_kind === 'integrity_error'

function Blocker({ action, reason }: { action: SceneAction | undefined; reason?: string }) {
  if (!action || action.available || !action.blocker) return null
  const leaves = !('site_part' in (action.blocker.home as object))
  const where = (action.blocker.home as { guided?: { section: string } }).guided?.section
  const words: Record<string, string> = { project: 'Site coordinates', loads: 'Loads', results: 'Results' }
  return (
    <span className="text-[11px] text-muted" data-testid={`scene-blocker-${action.action}`}>
      {reason ?? action.blocker.reason}
      {leaves && where && (
        <> <button type="button" onClick={() => goHome(action.blocker!.home)} className="text-accent hover:underline">
          {action.blocker.link} {words[where] ?? where}
        </button></>
      )}
    </span>
  )
}

const newGreenfield = (i: number): GreenfieldInput => ({ id: `b${i}`, center: [0, 0], width: 20, depth: 20, height: 10, heading: 0, unit: 'm' })

function GreenfieldRow({ g, onChange, onRemove, disabled }: {
  g: GreenfieldInput; onChange: (g: GreenfieldInput) => void; onRemove?: () => void; disabled?: boolean
}) {
  const f = (k: 'width' | 'depth' | 'height' | 'heading') => (v: number) => onChange({ ...g, [k]: v })
  const heightErr = !(g.height > 0 && g.height <= HEIGHT_MAX_M) ? `Enter above 0 and at most ${n(HEIGHT_MAX_M)} m` : null
  const sideErr = (v: number) => (v > 0 ? null : 'Enter above 0 m')
  const numErr = (v: number) => (Number.isFinite(v) ? null : 'Enter a number')
  return (
    <div className="flex flex-wrap items-end gap-2" data-testid={`greenfield-${g.id}`}>
      <NumberField label="East" unit="m" value={g.center[0]} onChange={v => onChange({ ...g, center: [v, g.center[1]] })} error={numErr(g.center[0])} width="w-16" disabled={disabled} />
      <NumberField label="North" unit="m" value={g.center[1]} onChange={v => onChange({ ...g, center: [g.center[0], v] })} error={numErr(g.center[1])} width="w-16" disabled={disabled} />
      <NumberField label="Width" unit="m" value={g.width} onChange={f('width')} error={sideErr(g.width)} width="w-16" disabled={disabled} />
      <NumberField label="Depth" unit="m" value={g.depth} onChange={f('depth')} error={sideErr(g.depth)} width="w-16" disabled={disabled} />
      <NumberField label="Height" unit="m" value={g.height} onChange={f('height')} error={heightErr} width="w-16" disabled={disabled} testId={`greenfield-${g.id}-height`} />
      <NumberField label="Heading" unit="°" value={g.heading} onChange={f('heading')} error={numErr(g.heading)} width="w-16" disabled={disabled} />
      {onRemove && <button type="button" onClick={onRemove} disabled={disabled} className="mb-1 text-[11px] text-muted hover:text-accent">Remove</button>}
    </div>
  )
}

export const greenfieldValid = (g: GreenfieldInput) =>
  g.width > 0 && g.depth > 0 && g.height > 0 && g.height <= HEIGHT_MAX_M && [g.heading, ...g.center].every(Number.isFinite)

export default function SiteScenePanel({ site, contextBuildings }: { site: Site; contextBuildings: ContextBuilding[] | null }) {
  const project = useUIStore(s => s.currentProject)
  const part = useUIStore(s => s.sitePart)
  const setPart = useUIStore(s => s.setSitePart)
  const selection = useUIStore(s => s.sitePartSelection)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const refusal = useSitesStore(s => s.sceneRefusal[project ?? '__local__'])
  const clearRefusal = useSitesStore(s => s.clearSceneRefusal)
  const { flow } = useProjectFlow()
  const scene = useSiteScene(site.id)
  const proposal = useSceneProposal(s => s.proposal)
  const setProposal = useSceneProposal(s => s.setProposal)
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<GreenfieldInput[]>([])
  const [edit, setEdit] = useState<{ id: string; g: GreenfieldInput | null; height: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ copy: string; reload: boolean; snapshots: boolean } | null>(null)

  const actions = flow?.scene?.actions ?? []
  const action = (name: string) => actions.find(a => a.action === name)
  const guard = evaluateMutation(readOnly, readOnlyReason)
  const blocked = guard.allowed ? null : guard.blockedMessage
  const enabled = scene.data?.state === 'enabled' ? scene.data : null
  const accepted: SceneBuilding[] = enabled?.buildings ?? []
  const sources: SourceInput[] = contextBuildings ? osmSourcesInside(site, contextBuildings) : []
  const mine = proposal && proposal.site_id === site.id ? proposal : null
  const selected = selection?.kind === 'building' ? accepted.find(b => b.id === selection.id) ?? null : null
  const moved = flow?.scene?.anchor_moved.includes(site.id) ?? false

  const run = async (fn: () => Promise<void>) => {
    if (!project) return
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      setError(e instanceof SitesNotSaved
        ? { copy: e.message, reload: false, snapshots: false }
        : { copy: sceneErrorCopy(e), reload: sceneErrorWantsReload(e), snapshots: unreadable(e) })
    } finally { setBusy(false) }
  }
  const generate = () => run(async () => {
    setProposal(await propose(qc, project!, site.id, 'generate_native', { sources, greenfield: drafts }))
  })
  const apply = () => run(async () => {
    await applyProposal(qc, project!, mine!)
    setProposal(null); setDrafts([]); setEdit(null)
  })
  const discard = () => run(async () => {
    try { await discardProposal(project!, mine!) } finally { setProposal(null) }
  })
  const previewEdit = () => run(async () => {
    if (!selected || !edit) return
    const inputs = edit.g
      ? { greenfield: [edit.g] }
      : { sources: [{
          namespace: selected.source.namespace as 'osm' | 'manual', source_id: selected.source.source_id,
          ring: selected.footprint.map(([x, y]) => { const p = fromLocal(site.origin, { x, y }); return [p.lng, p.lat] as [number, number] }),
          height_m: edit.height,
        }] }
    setProposal(await propose(qc, project!, site.id, 'edit_buildings', inputs))
  })
  const reload = () => { setError(null); setProposal(null); void scene.refetch(); void qc.invalidateQueries({ queryKey: ['flow', project] }) }
  const startEdit = (b: SceneBuilding) => setEdit({ id: b.id, g: greenfieldFromBuilding(b), height: b.height_m })

  const genAction = action('generate_native')
  const canWrite = !blocked && !busy && !!project
  const draftsOk = drafts.every(greenfieldValid)
  const editing = edit && selected && edit.id === selected.id ? edit : null
  const editOk = editing ? (editing.g ? greenfieldValid(editing.g) : editing.height > 0 && editing.height <= HEIGHT_MAX_M) : false

  return (
    <div data-testid="site-scene-panel" className="w-[min(26rem,100%)] rounded-lg border border-border bg-bg/95 p-3 text-[12px] flex flex-col gap-2 shadow">
      <div role="tablist" aria-label="Site 3D parts" className="flex items-center gap-1 text-[11px]">
        {PARTS.map((p, i) => (
          <span key={p.id} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden className="text-ink-400">·</span>}
            <button type="button" role="tab" aria-selected={part === p.id} data-testid={`site-part-${p.id}`} onClick={() => setPart(p.id)}
              className={part === p.id ? 'font-semibold text-text' : 'text-muted hover:text-text'}>
              {p.label}
            </button>
          </span>
        ))}
      </div>

      {moved && (
        <div data-testid="site-anchor-moved" className="text-[11px] text-warn">
          Site coordinates moved over 1 km from this site: the site stays where it was created{' '}
          <button type="button" onClick={() => openSiteAnchor({ fromSite3D: true })} className="text-accent hover:underline">› Site coordinates</button>
        </div>
      )}
      {refusal && (
        <div role="alert" className="flex items-center gap-2 text-[11px] text-danger">
          <span>{refusal}</span>
          <button type="button" onClick={() => clearRefusal(project)} className="text-muted hover:text-text">Dismiss</button>
        </div>
      )}
      {enabled?.unbound_keys && enabled.unbound_keys.length > 0 && (
        <div data-testid="site-unbound" className="text-[11px] text-warn">
          {enabled.unbound_keys.length} placed {enabled.unbound_keys.length === 1 ? 'item names' : 'items name'} equipment no longer in the model: the scene keeps their place until it is updated
        </div>
      )}
      {blocked && <div className="text-[11px] text-muted">{blocked}</div>}

      {part === 'map' && (
        <div className="flex flex-col gap-1 text-[11px] text-muted" data-testid="site-part-map-body">
          <span>{site.name} · boundary of {site.boundary.length} corners · {enabled ? `scene revision ${enabled.revision}` : 'not yet a 3D scene'}</span>
          {enabled && <span>The boundary is fixed: buildings can still be corrected in Generate.</span>}
        </div>
      )}

      {part === 'generate' && (
        <div className="flex flex-col gap-2" data-testid="site-part-generate-body">
          {scene.isError && (
            <span className="text-[11px] text-danger" data-testid="scene-read-error">
              {sceneErrorCopy(scene.error)}{unreadable(scene.error) && <> <SnapshotsLink /></>}
            </span>
          )}
          {mine ? (
            <div className="flex flex-col gap-1.5" data-testid="scene-proposal">
              <span className="text-text">
                Preview: {n(mine.preview.stats.buildings)} {mine.preview.stats.buildings === 1 ? 'building' : 'buildings'}
              </span>
              <span className="text-[11px] text-muted">Shown in red in the scene. Nothing is saved until Apply.</span>
              <div className="flex gap-2">
                <button type="button" data-testid="scene-apply" onClick={() => void apply()} disabled={!canWrite}
                  className="px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">Apply</button>
                <button type="button" data-testid="scene-discard" onClick={() => void discard()} disabled={busy}
                  className="px-3 py-1 rounded border border-border text-text hover:border-accent">Discard</button>
              </div>
            </div>
          ) : selected && enabled ? (
            <div className="flex flex-col gap-1.5" data-testid="scene-correction">
              <span className="text-text font-medium">
                {selected.source.namespace === 'osm' ? `OSM building ${selected.source.source_id}` : `Proposed building ${selected.source.source_id}`}
                <span className="text-muted font-normal"> · {heightEvidenceWords(selected)}</span>
              </span>
              {!editing ? (
                <div className="flex gap-2 items-center">
                  <span className="text-muted">Height {n(selected.height_m)} m</span>
                  <button type="button" data-testid="scene-correct" onClick={() => startEdit(selected)} disabled={!canWrite}
                    className="px-2 py-0.5 rounded border border-border text-text hover:border-accent disabled:opacity-50">Correct</button>
                  <button type="button" onClick={() => useUIStore.getState().setSitePartSelection(null)} className="text-[11px] text-muted hover:text-text">Deselect</button>
                </div>
              ) : (
                <>
                  {editing.g ? (
                    <GreenfieldRow g={editing.g} onChange={g => setEdit({ ...editing, g })} disabled={!canWrite} />
                  ) : (
                    <NumberField label="Height" unit="m" value={editing.height} testId="scene-correct-height"
                      onChange={h => setEdit({ ...editing, height: h })} disabled={!canWrite}
                      error={editing.height > 0 && editing.height <= HEIGHT_MAX_M ? null : `Enter above 0 and at most ${n(HEIGHT_MAX_M)} m`} />
                  )}
                  <div className="flex gap-2">
                    <button type="button" data-testid="scene-preview-change" onClick={() => void previewEdit()} disabled={!canWrite || !editOk}
                      className="px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">Preview change</button>
                    <button type="button" onClick={() => setEdit(null)} className="px-3 py-1 rounded border border-border text-text hover:border-accent">Cancel</button>
                  </div>
                  {!editOk && <span className="text-[11px] text-muted" data-testid="scene-preview-reason">{FIX_FIELDS}</span>}
                </>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {enabled && accepted.length > 0 && (
                <span className="text-text" data-testid="scene-accepted">
                  {n(accepted.length)} buildings accepted · click one to correct it
                </span>
              )}
              {!enabled && scene.data?.state === 'unadopted' && <span className="text-[11px] text-muted">{ADOPT_NOTE}</span>}
              <span className="text-[11px] text-muted" data-testid="scene-sources">
                {sources.length ? `Mapped buildings inside the boundary: ${n(sources.length)}` : NO_SOURCES}
              </span>
              {drafts.map((g, i) => (
                <GreenfieldRow key={g.id} g={g} disabled={!canWrite}
                  onChange={next => setDrafts(ds => ds.map((d, j) => (j === i ? next : d)))}
                  onRemove={() => setDrafts(ds => ds.filter((_, j) => j !== i))} />
              ))}
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" data-testid="scene-add-building" disabled={!canWrite}
                  onClick={() => setDrafts(ds => [...ds, newGreenfield(Date.now() % 1_000_000 + ds.length)])}
                  className="px-2 py-1 rounded border border-border text-text hover:border-accent disabled:opacity-50">Add building</button>
                <button type="button" data-testid="scene-generate" onClick={() => void generate()}
                  disabled={!canWrite || !genAction?.available || !draftsOk || (sources.length + drafts.length === 0)}
                  className="px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">
                  {accepted.length ? 'Generate again' : 'Generate buildings'}
                </button>
              </div>
              {!draftsOk && <span className="text-[11px] text-muted" data-testid="scene-generate-reason">{FIX_FIELDS}</span>}
              {accepted.length > 0 && <span className="text-[11px] text-muted">Generating again replaces the accepted buildings after you review it.</span>}
              {sources.length + drafts.length === 0 && genAction?.available && (
                <span className="text-[11px] text-muted">Nothing to generate: add a building or refresh the site context</span>
              )}
              <Blocker action={genAction} />
            </div>
          )}
        </div>
      )}

      {part === 'model' && (
        <div className="flex flex-col gap-1" data-testid="site-part-model-body">
          <span className="text-[11px] text-muted">Mapping buildings to model elements arrives with a later Site 3D slice.</span>
          <Blocker action={action('assign_native')} />
        </div>
      )}
      {part === 'results' && (
        <div className="flex flex-col gap-1" data-testid="site-part-results-body">
          <Blocker action={action('play_results')} reason={RESULTS_NOT_YET} />
        </div>
      )}

      {error && (
        <div role="alert" data-testid="scene-error" className="flex items-center gap-2 text-[11px] text-danger">
          <span>{error.copy}</span>
          {error.reload && <button type="button" onClick={reload} className="underline text-text hover:text-accent">Reload</button>}
          {error.snapshots && <SnapshotsLink />}
        </div>
      )}
    </div>
  )
}
