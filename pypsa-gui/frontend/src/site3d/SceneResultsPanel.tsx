// SC-03 (D07): the scene results details in the Site 3D column. DOM, no three.
//
// While the Eye is on, it reads the scene's native results at the timeline's
// snapshot (useSceneResults) and shows:
//  - a selected building: its attached items, each port's rate in its native
//    unit, an explicit share labelled allocated, and the groups it sits in;
//  - a group, picked from that list (or from all groups when no building is
//    selected): its boundary rows and per-carrier totals, as served;
//  - nothing selected: every attached item once (a shared one is one row).
// It subscribes to the store itself, so SiteCanvas never re-renders for a
// snapshot step; the timeline is read, never written.
import { useEffect, useState } from 'react'
import type { SiteScene } from '../api/siteScene'
import { sceneErrorCopy } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import { featureName, type UiMode } from './associations'
import { useSceneResults } from './useSceneResults'
import { featureView, groupsOf, groupView, runReason, unavailableWords, sceneSummary, snapshotWords, type GroupLine, type TargetView } from './sceneResults'

type EnabledScene = Extract<SiteScene, { state: 'enabled' }>

const valueClass = (available: boolean) => (available ? 'text-text tabular-nums' : 'text-muted italic')

function Target({ t, on }: { t: TargetView; on?: string[] }) {
  return (
    <li className="mt-1" data-testid="scene-result-target">
      <div className="text-text font-medium">
        {t.title}
        {t.allocation && <span className="font-normal text-muted" data-testid="scene-result-allocation"> · {t.allocation}</span>}
        {on && <span className="font-normal text-muted"> · on {on.join(', ')}</span>}
      </div>
      {t.reason && <div className="text-muted italic" data-testid="scene-result-reason">{t.reason}</div>}
      {t.level && <div className="pl-2 text-muted">level: <span className={valueClass(!t.level.startsWith('Unavailable'))}>{t.level}</span></div>}
      <ul className="pl-2">
        {t.ports.map(p => (
          <li key={p.key} data-testid="scene-result-port">
            <span className="text-muted">{p.label}: </span>
            <span className={valueClass(p.available)}>{p.value}</span>
            {p.tag && <span className="ml-1 rounded border border-border px-1 text-[10px] text-muted" data-testid="scene-result-tag">{p.tag}</span>}
            {p.notes.length > 0 && <span className="text-muted"> · {p.notes.join(' · ')}</span>}
          </li>
        ))}
      </ul>
    </li>
  )
}

function Lines({ title, rows, testId }: { title: string; rows: GroupLine[]; testId: string }) {
  if (!rows.length) return null
  return (
    <>
      <div className="mt-1 text-muted">{title}</div>
      <ul className="pl-2" data-testid={testId}>
        {rows.map(r => (
          <li key={r.key}><span className="text-muted">{r.label}: </span><span className={valueClass(r.available)}>{r.value}</span></li>
        ))}
      </ul>
    </>
  )
}

export default function SceneResultsPanel({ project, siteId, scene, current, freshSince }: {
  project: string | null
  siteId: string | null
  scene: EnabledScene | null
  current: boolean
  freshSince: number
}) {
  const eye = useUIStore(s => s.resultsOverlayEnabled)
  const idx = useUIStore(s => s.resultsSnapshotIdx)
  const mode = useUIStore(s => s.uiMode) as UiMode
  const selection = useUIStore(s => s.sitePartSelection)
  const [groupId, setGroupId] = useState<string | null>(null)
  const buildingId = selection?.kind === 'building' ? selection.id : null
  // Another building: a group picked for the last one does not carry over.
  useEffect(() => { setGroupId(null) }, [buildingId])

  const { data, query, loading, error } = useSceneResults({
    project, siteId, enabled: eye && !!scene, current, freshSince, sceneRevision: scene?.revision ?? null, idx,
  })
  if (!eye || !scene) return null

  const names = { buildings: scene.buildings, groups: scene.groups ?? [] }
  const groups = buildingId ? groupsOf(buildingId, names.groups) : names.groups
  const group = groupId && data?.groups[groupId] ? groupView(data.groups[groupId], mode, runReason(data)) : null

  let body: React.ReactNode
  if (!query) body = <p className="text-muted">This step is not a snapshot of the model.</p>
  else if (error) body = <p className="text-danger" role="alert">{sceneErrorCopy(error)}</p>
  else if (!data) body = <p className="text-muted">{loading ? 'Reading the results…' : 'No results'}</p>
  else {
    const views = buildingId ? featureView(data, buildingId, names, mode) : null
    const summary = buildingId ? null : sceneSummary(data, names, mode)
    body = (
      <>
        <p className="text-muted" data-testid="scene-results-snapshot">{snapshotWords(data)}</p>
        {!data.run.current && <p className="text-muted italic" data-testid="scene-results-run">{unavailableWords(data.run.reason)}</p>}
        {buildingId && <h4 className="mt-1 font-semibold text-text">{featureName(buildingId, names.buildings, names.groups)}</h4>}
        {views && (views.length
          ? <ul aria-label="Attached items">{views.map(t => <Target key={t.key} t={t} />)}</ul>
          : <p className="text-muted">Nothing is attached to this building.</p>)}
        {summary && (summary.length
          ? <ul aria-label="Attached items">{summary.map(r => <Target key={`${r.view.key}|${r.on.join()}`} t={r.view} on={r.on} />)}</ul>
          : <p className="text-muted">Nothing is attached to the scene’s buildings yet.</p>)}
        {groups.length > 0 && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1" role="group" aria-label="Groups">
            <span className="text-muted">{buildingId ? 'In group:' : 'Groups:'}</span>
            {groups.map(g => (
              <button key={g.id} type="button" aria-pressed={groupId === g.id} data-testid={`scene-result-group-${g.id}`}
                onClick={() => setGroupId(groupId === g.id ? null : g.id)}
                className={`rounded border px-1.5 text-[11px] ${groupId === g.id ? 'border-accent text-accent' : 'border-border text-text hover:border-accent'}`}>
                {g.label}
              </button>
            ))}
          </div>
        )}
        {groupId && (
          <section className="mt-1 rounded border border-border px-1.5 py-1" data-testid="scene-result-group" aria-label={`Group ${featureName(groupId, names.buildings, names.groups)}`}>
            <h4 className="font-semibold text-text">{featureName(groupId, names.buildings, names.groups)} · net import</h4>
            {group ? (
              <>
                <Lines title="Across the boundary, per bus" rows={group.boundary} testId="scene-result-boundary" />
                <Lines title="Per carrier" rows={group.totals} testId="scene-result-totals" />
                <div className="mt-1"><span className="text-muted" data-testid="scene-result-total-label">{group.totalLabel}: </span><span className={valueClass(!group.total.startsWith('Unavailable'))} data-testid="scene-result-total">{group.total}</span></div>
                {group.notes.map(n => <p key={n} className="text-muted">{n}</p>)}
              </>
            ) : <p className="text-muted">No result for this group.</p>}
          </section>
        )}
      </>
    )
  }
  return (
    <details open className="max-h-[40vh] overflow-auto rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow" data-testid="scene-results">
      <summary className="cursor-pointer select-none"><h3 className="inline font-semibold text-text">Scene results</h3></summary>
      {body}
    </details>
  )
}
