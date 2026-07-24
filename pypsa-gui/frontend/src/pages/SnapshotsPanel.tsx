import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, RotateCcw, Trash2, Plus, X, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi, type SnapshotInfo } from '../api/projects'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { invalidateNetworkQueries, formatRelativeTime } from '../utils/projectActions'
import { appLog } from '../store/simulationStore'
import { confirmToast } from '../utils/toasts'
import { PageBody, PageSection, Btn, Tag } from '../components/PageKit'

// SnapshotsPanel — vertical timeline of project snapshots with restore +
// delete, rendered as a full-page tab from the sidebar's PROJECT section.

// Snapshot "type" tag. The backend SnapshotInfo has no `type` field, so the
// category (scenario / stress / archive) is encoded as a `[type]` prefix on
// the snapshot message — `parseSnapType` pulls it back out for the badge and
// strips it from the displayed note. `current` is derived (newest snapshot),
// not stored.
const SNAP_TYPES = ['scenario', 'stress', 'archive'] as const
type SnapType = typeof SNAP_TYPES[number]
const SNAP_TYPE_TONE: Record<SnapType, 'purple' | 'warn' | 'neutral'> = {
  scenario: 'purple', stress: 'warn', archive: 'neutral',
}
function parseSnapType(message: string): { type: SnapType | null; text: string } {
  const m = message.match(/^\[(scenario|stress|archive)\]\s*([\s\S]*)$/)
  if (m) return { type: m[1] as SnapType, text: m[2] }
  return { type: null, text: message }
}
export default function SnapshotsPanel() {
  const qc = useQueryClient()
  const currentProject = useUIStore(s => s.currentProject)
  const markProjectSaved = useUIStore(s => s.markProjectSaved)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const setCompareRailOpen = useUIStore(s => s.setCompareRailOpen)
  const [showCreate, setShowCreate] = useState(false)
  // Restore-confirm modal state. We capture the project name AT THE TIME the
  // user clicked the row, so a project switch while the modal is open can't
  // retarget the restore at the new project's files. Without this snapshot,
  // `currentProject` in the mutation closure would be the NEW project name
  // and the restore would either 404 (sid not in new project) or, worse,
  // succeed against a same-named snapshot in the new project.
  const [restoreConfirm, setRestoreConfirm] = useState<
    { snapshot: SnapshotInfo; projectName: string } | null
  >(null)

  const { data: snapshots = [], isLoading, error } = useQuery({
    queryKey: ['snapshots-list', currentProject],
    queryFn: () => currentProject
      ? projectsApi.listSnapshots(currentProject)
      : Promise.resolve([]),
    enabled: !!currentProject,
    staleTime: 5_000,
  })

  const restoreMut = useMutation({
    mutationFn: ({ name, sid }: { name: string; sid: string }) =>
      projectsApi.restoreSnapshot(name, sid),
    onSuccess: (result, { name }) => {
      // Restore changes both the on-disk bundle AND the in-memory network, so
      // every network-derived query needs to refetch. Also bump the snapshot
      // list because the backend created a pre-restore safety snapshot.
      invalidateNetworkQueries(qc, name)
      qc.invalidateQueries({ queryKey: ['snapshots-list', name] })
      qc.invalidateQueries({ queryKey: nk(name, 'undoInfo') })
      // Stamp now() — the project files just got rewritten.
      markProjectSaved(name)
      toast.success(
        `Restored '${result.label || result.restored}' (${result.bus_count} buses)`,
        { duration: 4000 },
      )
      appLog('INFO',
        `Restored snapshot '${result.label || result.restored}' for project '${name}'`)
      setRestoreConfirm(null)
    },
    onError: (e: unknown) => {
      const msg = String((e as Error)?.message ?? e)
      toast.error('Restore failed')
      appLog('ERROR', `Restore failed: ${msg}`)
    },
  })

  const deleteMut = useMutation({
    mutationFn: ({ name, sid }: { name: string; sid: string }) =>
      projectsApi.deleteSnapshot(name, sid),
    onSuccess: (_r, { name, sid }) => {
      qc.invalidateQueries({ queryKey: ['snapshots-list', name] })
      toast.success(`Deleted snapshot`)
      appLog('INFO', `Deleted snapshot '${sid}' for project '${name}'`)
    },
    onError: (e: unknown) => {
      toast.error('Delete failed')
      appLog('ERROR', `Delete snapshot failed: ${String((e as Error)?.message ?? e)}`)
    },
  })

  if (!currentProject) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-8 text-muted text-sm gap-2">
        <Camera size={32} className="text-ink-300" />
        <span className="font-medium text-text">No project loaded</span>
        <span className="text-xs text-muted">
          Save a project first, then snapshots will appear here.
        </span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {showCreate && (
        <CreateSnapshotModal
          projectName={currentProject}
          onClose={() => setShowCreate(false)}
        />
      )}
      {restoreConfirm && (
        <RestoreConfirmModal
          snapshot={restoreConfirm.snapshot}
          projectName={restoreConfirm.projectName}
          pending={restoreMut.isPending}
          onConfirm={() => restoreMut.mutate({
            name: restoreConfirm.projectName,
            sid: restoreConfirm.snapshot.id,
          })}
          onClose={() => setRestoreConfirm(null)}
        />
      )}

      <PageBody>
        <PageSection
          title="All snapshots"
          count={snapshots.length}
          hint="point-in-time copies of the project state"
          right={
            <Btn variant="primary" onClick={() => setShowCreate(true)}>
              <Plus size={12} /> Save snapshot
            </Btn>
          }
          bodyClassName=""
        >
          {isLoading ? (
            <div className="px-4 py-8 text-[11.5px] text-muted text-center">Loading…</div>
          ) : error ? (
            <div className="px-4 py-8 text-[11.5px] text-danger text-center">Could not load snapshots</div>
          ) : snapshots.length === 0 ? (
            <div className="flex flex-col items-center text-center px-8 py-12 gap-2 text-muted text-sm">
              <Camera size={24} className="text-ink-300" />
              <span className="text-text font-medium">No snapshots yet</span>
              <span className="text-xs text-muted max-w-sm">
                Click <strong>Save snapshot</strong> above to checkpoint the current project state.
                Restores are reversible — each restore auto-snapshots the prior state.
              </span>
            </div>
          ) : (
            <div className="flex flex-col">
              {snapshots.map((snap, i) => (
                <SnapshotRow
                  key={snap.id}
                  snap={snap}
                  isNewest={i === 0}
                  onCompare={() => { setSlidePanel('results'); setCompareRailOpen(true) }}
                  onRestore={() => setRestoreConfirm({ snapshot: snap, projectName: currentProject })}
                  onDelete={() => {
                    // Inline confirm toast — non-blocking, styled, replaces native
                    // window.confirm() per the codebase's CLAUDE.md convention. We
                    // don't use Undo-toast here because snapshots have no backend
                    // soft-delete; once shutil.rmtree runs, the data is gone.
                    const label = snap.label || snap.id
                    confirmToast(
                      `Delete snapshot "${label}"?`,
                      () => deleteMut.mutate({ name: currentProject, sid: snap.id }),
                      { confirmLabel: 'Delete', danger: true },
                    )
                  }}
                  restoreDisabled={restoreMut.isPending}
                  deleteDisabled={deleteMut.isPending}
                />
              ))}
            </div>
          )}
        </PageSection>
      </PageBody>
    </div>
  )
}

function SnapshotRow({
  snap, isNewest, onCompare, onRestore, onDelete, restoreDisabled, deleteDisabled,
}: {
  snap: SnapshotInfo
  isNewest: boolean
  onCompare: () => void
  onRestore: () => void
  onDelete: () => void
  restoreDisabled: boolean
  deleteDisabled: boolean
}) {
  const relative = formatRelativeTime(snap.created_at) ?? '—'
  const objStr = snap.objective != null ? `obj €${(snap.objective / 1e9).toFixed(3)}B` : null
  // Dispatch-status badge. fresh = solver dispatch matches the snapshot's
  // topology; stale = dispatch is for a different topology (charts would
  // mislead until re-solve); none = no dispatch. Older snapshots without
  // `dispatch_status` fall through to the `has_results` boolean.
  const solved = snap.dispatch_status === 'fresh'
    || (snap.dispatch_status === undefined && snap.has_results)
  // User-chosen category (scenario / stress / archive), encoded as a `[type]`
  // prefix on the message. `current` (newest) takes precedence as the badge.
  const { type, text } = parseSnapType(snap.message)
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-t border-border first:border-t-0 hover:bg-bg-2 transition-colors group">
      {/* LED — solid green for the newest snapshot, hollow ring for older. */}
      <span
        aria-hidden
        className="shrink-0 w-2 h-2 rounded-full"
        style={{
          background: isNewest ? 'var(--color-success)' : 'transparent',
          border: isNewest ? 'none' : '1.5px solid var(--color-ink-400)',
        }}
      />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-[13px] font-semibold text-text truncate">
            {snap.label || <span className="text-muted italic font-normal">unlabeled</span>}
          </span>
          {isNewest ? <Tag tone="accent">current</Tag>
            : type ? <Tag tone={SNAP_TYPE_TONE[type]}>{type}</Tag>
            : null}
          {snap.dispatch_status === 'stale' ? (
            <span title="Solver dispatch is for a different generator/bus set than this snapshot contains. Charts will be misleading until you re-run the solver after restoring.">
              <Tag tone="warn">stale</Tag>
            </span>
          ) : solved ? (
            <span title="Snapshot contains solver results consistent with its topology">
              <Tag tone="ok">solved</Tag>
            </span>
          ) : null}
        </div>
        <div className="text-[10.5px] text-muted font-mono">
          captured {relative} · {snap.bus_count} buses · {snap.snapshot_count} snapshots
          {objStr && ` · ${objStr}`}
        </div>
        {text && (
          <div className="text-[11px] text-ink-600 mt-1 line-clamp-2">{text}</div>
        )}
      </div>

      {/* Actions — appear on row hover. Restore is primary, Delete is muted. */}
      <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <Btn onClick={onCompare} title="Compare scenarios side-by-side">
          Compare
        </Btn>
        <Btn
          onClick={onRestore}
          disabled={restoreDisabled}
          title="Restore this snapshot (current state is auto-saved)"
        >
          <RotateCcw size={11} /> Restore
        </Btn>
        <button
          onClick={onDelete}
          disabled={deleteDisabled}
          title="Delete this snapshot"
          className="p-1.5 text-muted hover:text-danger disabled:opacity-50 disabled:cursor-not-allowed transition-colors rounded"
        >
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  )
}

function CreateSnapshotModal({
  projectName, onClose,
}: {
  projectName: string
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [label, setLabel] = useState('')
  const [message, setMessage] = useState('')
  // Snapshot category — encoded as a `[type]` prefix on the message since the
  // backend SnapshotInfo has no dedicated field. '' = uncategorised.
  const [snapType, setSnapType] = useState<SnapType | ''>('')

  const createMut = useMutation({
    mutationFn: (args: { label: string; message: string }) =>
      projectsApi.createSnapshot(projectName, args.label, args.message),
    onSuccess: (snap) => {
      qc.invalidateQueries({ queryKey: ['snapshots-list', projectName] })
      toast.success(`Snapshot '${snap.label || snap.id}' saved`)
      appLog('INFO',
        `Created snapshot '${snap.label || snap.id}' (${snap.bus_count} buses)`)
      onClose()
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 404) {
        toast.error('Cannot snapshot: project not found on disk. Save first.')
      } else {
        toast.error('Snapshot failed')
      }
      appLog('ERROR', `Snapshot failed: ${String((e as Error)?.message ?? e)}`)
    },
  })

  return (
    <div className="fixed inset-0 z-[400] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-bg rounded-lg shadow-xl p-5 w-[400px] max-w-[90vw]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-base font-semibold text-text">Save snapshot</span>
          <button onClick={onClose} className="text-muted hover:text-text">
            <X size={16} />
          </button>
        </div>
        <p className="text-xs text-muted mb-4">
          Captures the current saved state of <strong>{projectName}</strong>.
          Snapshots use the project's last-saved files, so save first if you have unsaved edits.
        </p>
        <label className="block mb-3">
          <span className="text-xs text-muted block mb-1">Label</span>
          <input
            type="text"
            value={label}
            onChange={e => setLabel(e.target.value)}
            placeholder="e.g. pre-co2-cap, baseline-v2"
            maxLength={80}
            autoFocus
            className="w-full px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        </label>
        <label className="block mb-3">
          <span className="text-xs text-muted block mb-1">Type</span>
          <select
            value={snapType}
            onChange={e => setSnapType(e.target.value as SnapType | '')}
            className="w-full px-2.5 py-1.5 text-sm border border-border rounded bg-bg focus:outline-none focus:ring-1 focus:ring-accent/40"
          >
            <option value="">— Uncategorised —</option>
            <option value="scenario">Scenario</option>
            <option value="stress">Stress test</option>
            <option value="archive">Archive</option>
          </select>
        </label>
        <label className="block mb-4">
          <span className="text-xs text-muted block mb-1">Note <span className="text-muted/60">(optional)</span></span>
          <textarea
            value={message}
            onChange={e => setMessage(e.target.value)}
            placeholder="Why are you saving this snapshot?"
            maxLength={2000}
            rows={3}
            className="w-full px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:ring-1 focus:ring-accent/40 resize-none"
          />
        </label>
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-muted hover:text-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => createMut.mutate({
              label,
              // Prefix the message with the chosen type so SnapshotRow can
              // render the category badge — backend has no `type` field.
              message: snapType ? `[${snapType}] ${message}`.trim() : message,
            })}
            disabled={createMut.isPending}
            className="px-3 py-1.5 text-sm font-medium bg-accent text-white rounded hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {createMut.isPending ? 'Saving…' : 'Save snapshot'}
          </button>
        </div>
      </div>
    </div>
  )
}

function RestoreConfirmModal({
  snapshot, projectName, pending, onConfirm, onClose,
}: {
  snapshot: SnapshotInfo
  projectName: string
  pending: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  // Diff preview — fetch the current network's bus/snapshot counts so the
  // user can see what's actually changing. We rely on the standard `meta`
  // and `snapshots` queries (already cached by other consumers); both have
  // `staleTime` set elsewhere so this rarely triggers a request.
  const currentProject = useUIStore(s => s.currentProject)
  const { data: meta } = useQuery({ queryKey: nk(currentProject, 'meta'), queryFn: networkApi.getMeta, staleTime: 10_000 })
  const { data: snaps } = useQuery({ queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots, staleTime: 10_000 })

  // Compute the bus/snapshot diff between the snapshot and the live network.
  // null = unknown (still loading meta). We avoid showing partial diffs to
  // prevent users acting on incorrect information.
  const currentBuses = meta?.bus_count
  const currentSnaps = snaps?.count
  return (
    <div className="fixed inset-0 z-[400] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-bg rounded-lg shadow-xl p-5 w-[460px] max-w-[90vw]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 mb-3">
          <AlertTriangle className="text-warn shrink-0 mt-0.5" size={20} />
          <div className="flex-1">
            <div className="text-base font-semibold text-text mb-1">
              Restore snapshot?
            </div>
            <div className="text-xs text-muted">
              Project <strong>{projectName}</strong> will be replaced with the contents of
              <strong> "{snapshot.label || snapshot.id}"</strong>.
            </div>
          </div>
        </div>

        {/* Diff preview — what's actually changing. Only render when we have
            the meta + snapshots data for the live network; otherwise skip
            (don't show partial info). The diff is informational; the
            user's commit is still gated on the explicit Restore button. */}
        {currentBuses !== undefined && currentSnaps !== undefined && (
          <div className="bg-bg/60 border border-border rounded p-3 mb-3 text-xs">
            <div className="font-semibold text-text mb-2">Change preview</div>
            <DiffRow label="Buses"     before={currentBuses} after={snapshot.bus_count} />
            <DiffRow label="Snapshots" before={currentSnaps} after={snapshot.snapshot_count} />
            <DiffRow
              label="Solver dispatch"
              before={undefined}
              after={undefined}
              renderValue={
                snapshot.dispatch_status === 'stale'
                  ? <span className="text-warn font-semibold">stale (re-run after restore)</span>
                  : snapshot.dispatch_status === 'fresh' || snapshot.has_results
                  ? <span className="text-success font-semibold">included</span>
                  : <span className="text-muted">none</span>
              }
            />
          </div>
        )}

        <div className="bg-bg/60 border border-border rounded p-3 mb-4 text-xs text-muted">
          <div className="font-medium text-text mb-1">Safety net</div>
          The current state will be auto-saved as a snapshot labelled
          <strong> "before-restore"</strong> so you can roll back if this isn't what you wanted.
        </div>
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            disabled={pending}
            className="px-3 py-1.5 text-sm text-muted hover:text-text disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={pending}
            className="px-3 py-1.5 text-sm font-medium bg-accent text-white rounded hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {pending ? 'Restoring…' : 'Restore'}
          </button>
        </div>
      </div>
    </div>
  )
}

// Single before-→-after row used by the diff preview. Optional `renderValue`
// override lets the caller drop in non-numeric content (e.g. "stale" badge
// for dispatch).
function DiffRow({ label, before, after, renderValue }: {
  label: string
  before?: number
  after?: number
  renderValue?: React.ReactNode
}) {
  const sameNumeric = typeof before === 'number' && typeof after === 'number' && before === after
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-muted shrink-0" style={{ width: 110 }}>{label}</span>
      {renderValue ? (
        <span className="font-mono">{renderValue}</span>
      ) : (
        <span className="font-mono">
          <span className={sameNumeric ? 'text-muted' : 'text-text'}>{before ?? '—'}</span>
          <span className="text-muted/60 mx-1.5">→</span>
          <span className={sameNumeric ? 'text-muted' : 'text-accent font-semibold'}>{after ?? '—'}</span>
          {!sameNumeric && typeof before === 'number' && typeof after === 'number' && (
            <span className="text-muted/60 ml-1.5 text-[10px]">
              ({after > before ? '+' : ''}{after - before})
            </span>
          )}
        </span>
      )}
    </div>
  )
}
