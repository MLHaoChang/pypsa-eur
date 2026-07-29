import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Box, Save, Pencil, Check, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { bundleErrorMessage, projectsApi } from '../api/projects'
import { changelogApi, type ChangeLogEntry } from '../api/changelog'
import { downloadProjectBundle, formatRelativeTime, saveProjectQuietly } from '../utils/projectActions'
import { isRenewableCarrier } from './results/shared'
import { PageHeader, PageBody, PageSection, RowGrid, StatCard, Btn, BarList } from '../components/PageKit'
import type { Bus, Generator, StorageUnit, Load, Link } from '../api/types'

// OverviewPanel — the design's "Project info" page: a PageHeader, a row of four
// StatCards (buses / lines+transformers / installed capacity / last solve), and
// a two-column body of Capacity mix (bar list) + Recent activity.
//
// Rendered as a full-page tab from the sidebar's PROJECT section.
export default function OverviewPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const lastSavedByProject = useUIStore(s => s.lastSavedByProject)
  const autosaveEnabled = useUIStore(s => s.autosaveEnabled)
  const markProjectSaved = useUIStore(s => s.markProjectSaved)
  const renameProjectInStore = useUIStore(s => s.renameProject)

  // Guards the bundle export. Without it a double-click ran two exports: two
  // save dialogs, two full server-side zip builds, and two success toasts for
  // one intent. Same shape as ImportExport's Download button.
  const [exporting, setExporting] = useState(false)

  // Network-level data. All cached by other consumers, so this page rarely
  // triggers fresh requests.
  const { data: meta }            = useQuery({ queryKey: nk(currentProject, 'meta'),               queryFn: networkApi.getMeta,         staleTime: 5_000 })
  const { data: status }          = useQuery({ queryKey: nk(currentProject, 'simulationStatus'),   queryFn: simulationApi.getStatus,    staleTime: 5_000 })
  const { data: buses = [] }        = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses,        staleTime: 30_000 })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines,        staleTime: 30_000 })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers, staleTime: 30_000 })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks,        staleTime: 30_000 })
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators,   staleTime: 30_000 })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits, staleTime: 30_000 })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads,        staleTime: 30_000 })

  const { data: changelog = [] } = useQuery({
    queryKey: ['changelog'],
    queryFn: changelogApi.getAll,
    refetchInterval: 10_000,
    staleTime: 3_000,
  })
  const recentActivity = useMemo(() => changelog.slice(0, 8), [changelog])

  // Capacity roll-ups by category — split generators renewable vs thermal,
  // sum storage p_nom and load p_set. All in MW.
  const caps = useMemo(() => {
    let renew = 0, thermal = 0
    for (const g of generators as Generator[]) {
      if (isRenewableCarrier(g.carrier ?? '')) renew += g.p_nom ?? 0
      else thermal += g.p_nom ?? 0
    }
    const storage = (storageUnits as StorageUnit[]).reduce((s, su) => s + (su.p_nom ?? 0), 0)
    const load = (loads as Load[]).reduce((s, l) => s + (l.p_set ?? 0), 0)
    // Links: sum p_nom (MW conversion / transmission capacity). PyPSA Links
    // model HVDC, electrolysers, fuel cells, heat pumps, methanation — any
    // controllable energy flow with a per-bus0→bus1 efficiency.
    const linkPower = (links as Link[]).reduce((s, k) => s + (k.p_nom ?? 0), 0)
    return { renew, thermal, storage, load, linkPower, totalGen: renew + thermal }
  }, [generators, storageUnits, loads, links])

  // Distinct link carriers (HVDC, H2 pipeline, electrolyzer, …) — surfaced
  // on the Links StatCard sub-line so the user knows at a glance what kind
  // of conversion / interconnect equipment the project carries.
  const linkSummary = useMemo(() => {
    const carriers = new Map<string, number>()
    for (const k of links as Link[]) {
      const c = (k.carrier && k.carrier.trim()) || 'unspecified'
      carriers.set(c, (carriers.get(c) ?? 0) + 1)
    }
    return {
      count: (links as Link[]).length,
      carriers,
    }
  }, [links])

  // Bus split — transmission ≥ 100 kV, distribution below.
  const busCount = meta?.bus_count ?? (buses as Bus[]).length
  const transmission = (buses as Bus[]).filter(b => (b.v_nom ?? 0) >= 100).length
  const distribution = Math.max(0, busCount - transmission)

  const lastSavedIso = currentProject ? lastSavedByProject[currentProject] ?? null : null
  const lastSavedRel = formatRelativeTime(lastSavedIso)

  // Objective — split value + unit so the StatCard renders the unit faintly.
  // Guard against NaN (HiGHS can write NaN to solver state on aborted runs).
  const obj = status?.objective
  const objValid = obj != null && Number.isFinite(obj)
  const objStr = !objValid ? '—'
    : Math.abs(obj) >= 1e9 ? (obj / 1e9).toFixed(2)
    : Math.abs(obj) >= 1e6 ? (obj / 1e6).toFixed(2)
    : Math.abs(obj) >= 1e3 ? (obj / 1e3).toFixed(2)
    : obj.toFixed(2)
  const objUnit = !objValid ? ''
    : Math.abs(obj) >= 1e9 ? ' B€'
    : Math.abs(obj) >= 1e6 ? ' M€'
    : Math.abs(obj) >= 1e3 ? ' k€'
    : ' €'

  const totalGenStr = caps.totalGen >= 1000
    ? (caps.totalGen / 1000).toFixed(2)
    : caps.totalGen.toFixed(0)
  const totalGenUnit = caps.totalGen >= 1000 ? ' GW' : ' MW'

  const handleSave = async () => {
    if (!currentProject) return
    const ok = await saveProjectQuietly(currentProject)
    if (ok) {
      markProjectSaved(currentProject)
      toast.success(`Saved '${currentProject}'`)
    } else {
      toast.error('Save failed')
    }
  }

  // Empty state — no project loaded yet.
  if (!currentProject) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-8 text-muted text-sm gap-3">
        <FolderOpen size={36} className="text-ink-300" />
        <span className="font-medium text-text">No project loaded</span>
        <span className="text-xs text-muted max-w-xs">
          Open one from the sidebar's <strong>Workspace</strong> section, create a
          new project, or import a <span className="font-mono">.pypsaproj.zip</span> bundle.
        </span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        eyebrow="PROJECT · OVERVIEW"
        title={
          <ProjectNameEditor
            currentProject={currentProject}
            onRenamed={(oldName, newName) => renameProjectInStore(oldName, newName)}
          />
        }
        subtitle="Snapshot of the current network configuration, capacity mix, and recent activity."
        actions={
          <>
            <Btn
              disabled={exporting}
              // Fetch THEN save, via the same helper AppHeader and the Sidebar
              // modal use. Three things this is not:
              //   * not `window.open` — it returns null and downloads nothing
              //     inside the desktop shell (measured on a real WKWebView).
              //   * not a bare anchor at the API URL — an anchor cannot see a
              //     status code, so a 401/403/404 JSON body would be written
              //     to disk as a `.pypsaproj.zip` with nothing to indicate it.
              //   * not the helper's DEFAULT options. An export is a one-off
              //     copy, not a Save: `askLocation` so it can never silently
              //     rewrite the file the user chose for Save, and `skipCache`
              //     so a later Save can never silently overwrite this export.
              //     Same pair, same reason, as "Save a Copy".
              // No `if (exporting) return` guard here: `disabled` above already
              // blocks the second click, and a browser fires no click event on
              // a disabled button at all. Mutation testing confirmed it —
              // deleting the guard changed nothing, deleting `disabled` broke
              // the double-click test. An unreachable guard is a line that
              // looks tested and is not.
              onClick={async () => {
                setExporting(true)
                try {
                  const mode = await downloadProjectBundle(currentProject, {
                    askLocation: true,
                    skipCache: true,
                    skipErrorToast: true,
                  })
                  if (mode !== 'cancelled') {
                    toast.success(`Exported ${currentProject}.pypsaproj.zip`)
                  }
                } catch (e) {
                  const why = await bundleErrorMessage(e)
                  // `skipErrorToast` suppresses the interceptor's appLog line
                  // as well as its toast — one flag gates both. Without this
                  // the export is the only one of the three bundle-export
                  // paths with no entry in the Log tab, and toasts dismiss
                  // themselves.
                  appLog('ERROR', `Export bundle '${currentProject}' failed: ${why}`)
                  toast.error(`Export failed: ${why}`)
                } finally {
                  setExporting(false)
                }
              }}
              title="Download the project as a .pypsaproj.zip bundle"
            >
              <Box size={12} /> Export bundle
            </Btn>
            <Btn variant="primary" onClick={handleSave} title="Save the project to disk">
              <Save size={12} /> Save
            </Btn>
          </>
        }
      />
      <PageBody>
        {/* ── StatCard strip ─────────────────────────────────────── */}
        <RowGrid cols={5}>
          <StatCard
            accent
            eyebrow="Buses"
            value={busCount}
            sub={`${transmission} transmission · ${distribution} distribution`}
          />
          <StatCard
            eyebrow="Lines + transformers"
            value={lines.length + transformers.length}
            sub={`${lines.length} AC · ${transformers.length} transformer${transformers.length === 1 ? '' : 's'}`}
          />
          <StatCard
            eyebrow="Links"
            value={linkSummary.count}
            sub={linkSummary.count === 0
              ? 'No HVDC / converter / pipeline links'
              : (() => {
                  // Show up to two top carriers verbatim; lump the rest into a
                  // "… +N more" so the sub-line stays single-line on a 5-col strip.
                  const entries = Array.from(linkSummary.carriers.entries())
                    .sort((a, b) => b[1] - a[1])
                  const head = entries.slice(0, 2)
                    .map(([c, n]) => `${n} ${c}`)
                    .join(' · ')
                  const rest = entries.length - 2
                  return rest > 0 ? `${head} · +${rest} more` : head
                })()}
            title={`Total link capacity: ${caps.linkPower.toFixed(0)} MW (p_nom). Carriers: ${
              Array.from(linkSummary.carriers.entries())
                .map(([c, n]) => `${c} (${n})`).join(', ') || 'none'
            }`}
          />
          <StatCard
            eyebrow="Installed capacity"
            value={totalGenStr}
            unit={totalGenUnit}
            sub={`Storage ${caps.storage.toFixed(0)} MW · Load ${caps.load.toFixed(0)} MW`}
          />
          <StatCard
            eyebrow="Last solve"
            value={objStr}
            unit={objUnit}
            delta={status?.condition === 'optimal' ? 'Optimal'
              : status?.condition ? status.condition : 'Not run'}
            deltaState={status?.condition === 'optimal' ? 'ok' : undefined}
            sub={status?.solve_time != null
              ? `${status.solve_time.toFixed(1)} s · ${lastSavedRel ? `saved ${lastSavedRel}` : 'unsaved'}`
              : `Click Run LOPF · autosave ${autosaveEnabled ? 'on' : 'off'}`}
          />
        </RowGrid>

        {/* ── Capacity mix + Recent activity ─────────────────────── */}
        <RowGrid cols={2}>
          <PageSection title="Capacity mix" hint="By category">
            <BarList
              items={[
                { label: 'Renewables', value: caps.renew,     display: `${caps.renew.toFixed(0)} MW`,     color: 'var(--color-cat-renew)' },
                { label: 'Thermal',    value: caps.thermal,   display: `${caps.thermal.toFixed(0)} MW`,   color: 'var(--color-cat-thermal)' },
                { label: 'Load',       value: caps.load,      display: `${caps.load.toFixed(0)} MW`,      color: 'var(--color-cat-load)' },
                { label: 'Storage',    value: caps.storage,   display: `${caps.storage.toFixed(0)} MW`,   color: 'var(--color-cat-storage)' },
                // Links: only show when the project actually carries any —
                // hides a "0 MW" entry for plain electricity-only projects.
                ...(caps.linkPower > 0 || linkSummary.count > 0 ? [{
                  label: 'Links',
                  value: caps.linkPower,
                  display: `${caps.linkPower.toFixed(0)} MW`,
                  color: 'var(--color-accent)',
                }] : []),
              ]}
            />
          </PageSection>

          <PageSection title="Recent activity" count={recentActivity.length} bodyClassName="">
            {recentActivity.length === 0 ? (
              <div className="text-xs text-muted py-6 text-center">
                No activity yet. Edits, saves, and runs will appear here.
              </div>
            ) : (
              <div className="flex flex-col">
                {recentActivity.map(e => (
                  <ActivityRow key={e.id} entry={e} />
                ))}
              </div>
            )}
          </PageSection>
        </RowGrid>
      </PageBody>
    </div>
  )
}

// ── Project-name inline editor ──────────────────────────────────────────────
// Click the title → it swaps to an input. Enter / Check icon commits the
// rename through `POST /api/projects/{name}/rename` (atomic dir move +
// metadata refresh + child-scenario reparent). Esc / X icon cancels.
//
// On success: updates currentProject + openTabs + recents + lastSavedByProject
// atomically via the `renameProject` store action, then invalidates every
// React-Query cache keyed by the old name so the rest of the UI re-fetches
// against the new path.
//
// Charset rule mirrors the backend's `_PROJECT_NAME_RE`: letters, digits,
// underscore, hyphen, dot, space. We validate client-side before firing the
// mutation so the user gets instant feedback (rather than waiting for the
// 400 round-trip on every keystroke).

const PROJECT_NAME_VALID = /^[A-Za-z0-9_.\- ]{1,64}$/

function ProjectNameEditor({
  currentProject, onRenamed,
}: {
  currentProject: string
  onRenamed: (oldName: string, newName: string) => void
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(currentProject)
  const inputRef = useRef<HTMLInputElement>(null)

  // Reset the draft when the active project changes externally (e.g. user
  // switched projects via the sidebar while this panel was open) — without
  // this, opening the editor would show the previous project's name.
  useEffect(() => {
    if (!editing) setDraft(currentProject)
  }, [currentProject, editing])

  // Focus + select-all on transition into edit mode. Selecting makes Type-
  // over-to-replace work as expected.
  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const rename = useMutation({
    mutationFn: (newName: string) => projectsApi.rename(currentProject, newName),
    onSuccess: (_info, newName) => {
      const oldName = currentProject
      onRenamed(oldName, newName)
      // Invalidate every cache keyed by the old name. ['projects'] is the
      // project-list that the sidebar / scenario picker reads from.
      qc.invalidateQueries({ queryKey: ['projects'] })
      qc.invalidateQueries({ queryKey: ['compare-state', oldName] })
      qc.invalidateQueries({ queryKey: ['results-summary', oldName] })
      qc.invalidateQueries({ queryKey: nk(oldName, 'meta') })
      qc.invalidateQueries({ queryKey: ['changelog'] })
      toast.success(`Renamed to '${newName}'`)
      setEditing(false)
    },
    onError: (e: { response?: { data?: { detail?: unknown } }; message?: string }) => {
      const detail = e.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : (e.message || 'Rename failed'))
    },
  })

  const trimmed = draft.trim()
  const valid =
    trimmed.length >= 1 &&
    trimmed.length <= 64 &&
    PROJECT_NAME_VALID.test(trimmed) &&
    trimmed !== currentProject

  const commit = () => {
    if (!valid) {
      // Bail out quietly — the disabled state on the commit button is the
      // primary affordance; toast would be noisy on every Esc-tab away.
      setEditing(false)
      setDraft(currentProject)
      return
    }
    rename.mutate(trimmed)
  }

  const cancel = () => {
    setEditing(false)
    setDraft(currentProject)
  }

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        title="Click to rename project"
        // The PageHeader title slot is sized for an <h1>; we mimic that to keep
        // the visual rhythm consistent. Pencil icon appears on hover only so
        // the static state stays clean.
        className="group inline-flex items-center gap-2 text-[22px] font-semibold text-text tracking-[-0.02em] leading-tight hover:text-accent transition-colors max-w-full"
      >
        <span className="truncate">{currentProject}</span>
        <Pencil size={14} className="opacity-0 group-hover:opacity-60 transition-opacity shrink-0" />
      </button>
    )
  }

  return (
    <div className="flex items-center gap-2 max-w-full">
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); commit() }
          else if (e.key === 'Escape') { e.preventDefault(); cancel() }
        }}
        maxLength={64}
        disabled={rename.isPending}
        // Inherit the title typography so the input doesn't visually jump
        // when toggling between view and edit modes.
        className="bg-bg border border-accent rounded px-2 py-1 text-[22px] font-semibold text-text tracking-[-0.02em] leading-tight focus:outline-none min-w-[200px] max-w-[440px] disabled:opacity-60"
      />
      <button
        onClick={commit}
        disabled={!valid || rename.isPending}
        title="Confirm rename (Enter)"
        className="flex items-center justify-center w-7 h-7 rounded bg-success text-white hover:bg-success/85 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <Check size={14} />
      </button>
      <button
        onClick={cancel}
        disabled={rename.isPending}
        title="Cancel (Esc)"
        className="flex items-center justify-center w-7 h-7 rounded bg-bg border border-border text-muted hover:text-text hover:bg-border/30 transition-colors disabled:opacity-30"
      >
        <X size={14} />
      </button>
      {!valid && trimmed && trimmed !== currentProject && (
        <span className="text-[10px] text-danger">
          Allowed: letters, digits, _ . - and space (max 64)
        </span>
      )}
    </div>
  )
}

// ── Recent activity row ─────────────────────────────────────────────────────
// Action verb is colour-coded so a long list scans like a git log — saves /
// loads are neutral, deletes are red, etc.
const ACTION_COLORS: Record<string, string> = {
  add:                  'text-success',
  update:               'text-text',
  delete:               'text-danger',
  undo:                 'text-warn',
  import:               'text-accent',
  export:               'text-accent',
  save:                 'text-success',
  load:                 'text-accent',
  timeseries:           'text-accent',
  dispatch_invalidated: 'text-warn',
  snapshot:             'text-accent',
  restore:              'text-warn',
  delete_snapshot:      'text-danger',
  warn:                 'text-warn',
  scenario_create:      'text-accent',
  delete_project:       'text-danger',
  cluster:              'text-accent',
}

// Short display labels for action verbs too long for the fixed-width badge
// cell. Falls through to entry.action (uppercased) for anything unlisted.
const ACTION_LABELS: Record<string, string> = {
  dispatch_invalidated: 'INVALID',
  delete_snapshot:      'SNAP DEL',
  timeseries:           'TIMSER',
  scenario_create:      'SCEN NEW',
  delete_project:       'PROJ DEL',
}

function ActivityRow({ entry }: { entry: ChangeLogEntry }) {
  const relative = formatRelativeTime(entry.timestamp) ?? '—'
  const cls = ACTION_COLORS[entry.action] ?? 'text-muted'
  const label = ACTION_LABELS[entry.action] ?? entry.action.toUpperCase()
  return (
    <div className="flex items-center gap-2.5 px-4 py-1.5 text-[11px] border-t border-border first:border-t-0 hover:bg-bg-2 transition-colors">
      <span
        className={`shrink-0 font-mono text-[9px] uppercase font-bold tracking-[0.06em] overflow-hidden whitespace-nowrap ${cls}`}
        style={{ width: 64, textOverflow: 'ellipsis' }}
        title={entry.action}
      >
        {label}
      </span>
      <span className="flex-1 min-w-0 text-ink-700 truncate" title={entry.description}>
        {entry.description}
      </span>
      <span className="shrink-0 text-[9.5px] text-muted font-mono ml-2">{relative}</span>
    </div>
  )
}
