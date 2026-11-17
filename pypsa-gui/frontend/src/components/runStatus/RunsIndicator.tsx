import { useEffect, useRef, useState } from 'react'
import { useUIStore } from '../../store/uiStore'
import { useSolveQueue } from '../../hooks/useSolveQueue'
import { useProjectRun } from '../../hooks/useProjectRun'
import { isActive } from '../../api/solveQueue'
import { runFromJob, type RunView } from '../../utils/runStatus'
import { RunStatusLine } from './RunStatusLine'

interface ListedRun { key: string; project: string; run: RunView }

/**
 * The header's Runs indicator (run progress spec §4, slice 2b; UX plan GO 1, 2).
 * The runs going on in the projects you can see: queue jobs of every project,
 * plus the Guided goal study of the project on screen. Not colour alone: it
 * says "N runs active" with the count. Expert opens the Solve queue panel;
 * Guided opens a list in place (Guided never switches mode, EXPERT-GUIDED-1),
 * where each run shows its full line, so text the strip cuts still exists here.
 * Renders nothing while no run is active.
 */
export function RunsIndicator() {
  const project = useUIStore(s => s.currentProject)
  const guided = useUIStore(s => s.uiMode === 'guided')
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const { data: queue } = useSolveQueue()
  const projectRun = useProjectRun()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  const runs: ListedRun[] = (queue?.jobs ?? [])
    .filter(j => isActive(j) && j.project_id != null)
    .map(j => ({ key: j.id, project: j.project_id!, run: runFromJob(j) }))
  if (projectRun && projectRun.jobId == null && project) {
    runs.push({ key: 'study', project, run: projectRun })
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  useEffect(() => { if (runs.length === 0) setOpen(false) }, [runs.length])

  if (runs.length === 0) return null
  const label = `${runs.length} run${runs.length === 1 ? '' : 's'} active`
  return (
    <div ref={box} className="relative shrink-0">
      <button type="button" data-testid="runs-indicator"
        aria-expanded={guided ? open : undefined}
        aria-haspopup={guided ? 'dialog' : undefined}
        title={guided ? 'Show the runs going on' : 'Open the Solve queue'}
        onClick={() => (guided ? setOpen(o => !o) : setSlidePanel('solveQueue'))}
        className="flex items-center gap-1.5 h-7 px-2.5 rounded-md border border-amber-500/40 bg-amber-500/10 text-[10px] font-semibold text-amber-700 dark:text-amber-300 whitespace-nowrap">
        <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-warn" />
        {label}
      </button>
      {guided && open && (
        <div role="dialog" aria-label="Runs going on" data-testid="runs-popover"
          className="absolute right-0 top-8 z-50 w-[26rem] max-w-[90vw] rounded-lg border border-border bg-panel p-2 shadow-lg text-[11px]">
          <ul className="flex flex-col gap-1.5">
            {runs.map(r => (
              <li key={r.key} className="flex flex-col gap-0.5 rounded-md bg-bg-2 px-2 py-1.5 text-text">
                <span className="text-[10px] text-muted">{r.project}</span>
                <RunStatusLine run={r.run} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
