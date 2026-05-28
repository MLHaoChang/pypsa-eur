import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { formatRelativeTime } from '../utils/projectActions'

const STATUS_COLORS: Record<string, string> = {
  idle: 'text-muted', running: 'text-warn', completed: 'text-success',
  failed: 'text-danger', aborted: 'text-muted',
}

export default function StatusBar() {
  const { status, objective, solveTime } = useSimulationStore()
  const projectName = useUIStore(s => s.projectName)
  const currentProject = useUIStore(s => s.currentProject)
  const autosaveEnabled = useUIStore(s => s.autosaveEnabled)
  const lastSavedByProject = useUIStore(s => s.lastSavedByProject)

  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: networkApi.getMeta })
  // Snapshots are invalidated by every import / load / time-series upload —
  // staleTime: 0 makes the count update immediately when the cache is busted.
  const { data: snaps } = useQuery({
    queryKey: ['snapshots'],
    queryFn: networkApi.getSnapshots,
    staleTime: 0,
  })
  // Unsaved-edits indicator. The undo depth is the count of mutations since
  // the last explicit Save (which clears the stack as a checkpoint).
  const { data: undoInfo } = useQuery({
    queryKey: ['undoInfo'],
    queryFn: networkApi.undoInfo,
    refetchInterval: 3000,
    staleTime: 0,
  })
  const dirty = (undoInfo?.depth ?? 0) > 0
  const dirtyCount = undoInfo?.depth ?? 0

  // Re-render every minute so "Saved Xm ago" updates without polling backend.
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])

  const color = STATUS_COLORS[status] ?? 'text-muted'
  const objStr = objective != null ? `${(objective / 1e9).toFixed(3)} B€` : '—'

  // Prefer the UI store's project name (set on import/load/save) over PyPSA's
  // internal n.name, which defaults to "Unnamed Network" when not explicitly
  // assigned. Fall back to meta.name only when neither store value is set.
  const displayName = currentProject || projectName || meta?.name || 'Untitled'
  const lastSavedIso = currentProject ? lastSavedByProject[currentProject] ?? null : null
  const relative = formatRelativeTime(lastSavedIso)
  // Status text for the project saved-state: dirty wins over saved (a save 5m
  // ago is irrelevant once the user has made new edits).
  const savedLine = dirty
    ? (dirtyCount === 1 ? '1 unsaved change' : `${dirtyCount} unsaved changes`)
    : relative ? `Saved ${relative}`
    : null

  return (
    <div
      className="relative flex items-center gap-3 px-3 h-6 border-t border-border text-[10.5px] font-mono shrink-0"
      style={{ background: 'linear-gradient(180deg, var(--color-bg-2) 0%, var(--color-panel) 100%)' }}
    >
      {/* 1px white hairline highlight along the top edge — design system detail */}
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px pointer-events-none"
        style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.5) 50%, transparent)' }}
      />
      {/* Solver status */}
      <span className={`flex items-center gap-1 ${color}`}>
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
      <span className="text-border">|</span>

      {/* Project name + dirty dot */}
      <span className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: dirty ? '#d97706' : '#16a34a' }}
          title={dirty ? `${dirtyCount} unsaved edit${dirtyCount === 1 ? '' : 's'}` : 'Project is up-to-date'}
        />
        <span className="text-muted">{displayName}</span>
      </span>

      {savedLine && (
        <>
          <span className="text-border">|</span>
          <span className="text-muted">{savedLine}</span>
        </>
      )}

      <span className="text-border">|</span>
      <span className="text-muted">
        Autosave {autosaveEnabled ? 'on' : 'off'}
      </span>

      <span className="text-border">|</span>
      <span className="text-muted">{snaps?.count ?? 0} snapshots</span>

      <span className="ml-auto text-muted">Obj: {objStr}</span>
      {solveTime != null && <span className="text-muted">{solveTime}s</span>}
    </div>
  )
}
