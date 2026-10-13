import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ListChecks, Settings2 } from 'lucide-react'
import { useUIStore } from '../../store/uiStore'
import { simulationApi } from '../../api/simulation'
import { useSolveQueue } from '../../hooks/useSolveQueue'
import { isActive } from '../../api/solveQueue'
import { nk } from '../../utils/queryKeys'
import { SItem, usePanelToggle } from './SidebarRows'

/** "2 errors · 1 warning": the Issues badge says what its number counts. */
function issueBadgeTitle(errors: number, warnings: number): string {
  const part = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
  return [errors > 0 && part(errors, 'error'), warnings > 0 && part(warnings, 'warning')]
    .filter(Boolean).join(' · ')
}

// RUN: how the model is solved, what would stop it, and the queue of solves.
export default function RunSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const currentProject = useUIStore(s => s.currentProject)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  // Polls /preflight to surface the error+warning count as a badge on the
  // Issues row. 30 s feels right for an out-of-view indicator — long enough
  // not to thrash the backend, short enough that the badge stays roughly
  // accurate after an edit. The panel itself polls more aggressively when
  // open.
  const { data: preflight, isError: preflightErrored } = useQuery({
    queryKey: nk(currentProject, 'preflight'),
    queryFn: simulationApi.preflight,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  // ADR-0001: a failed preflight fetch must never render as "no issues" — a
  // defaulted zero here is indistinguishable from a real clean result, so it
  // would silently convert "we could not check" into "we checked and it's
  // fine". `preflightErrored` (covers both isError and the resulting
  // `data === undefined`) drives an explicit unavailable badge instead.
  const issueCount = (preflight?.errors ?? 0) + (preflight?.warnings ?? 0)
  const hasErrors = (preflight?.errors ?? 0) > 0
  // Active (queued + running) solve-queue jobs, surfaced as a badge on the
  // Solve queue row. Shares the ['solveQueue'] query — polls only while jobs
  // are active (see useSolveQueue), so this idle-mounted consumer is cheap.
  const { data: solveQueue } = useSolveQueue()
  const activeQueueCount = (solveQueue?.jobs ?? []).filter(isActive).length
  return (
    <div>
      <SItem icon={<Settings2 size={15} />} label="Solver Settings"
        title="Choose the solver, foresight mode (overnight / myopic / perfect), CO₂ price, discount rate and value-of-lost-load."
        active={activeSlidePanel === 'simparams'}
        onClick={() => toggle('simparams')}
      />
      <SItem
        icon={<AlertTriangle size={15} />}
        label="Issues"
        active={activeSlidePanel === 'issues'}
        rightEl={preflightErrored ? (
          <span
            className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{ background: 'rgba(148,163,184,0.18)', color: '#94a3b8' }}
            title="Could not check for issues — the preflight validation request failed"
          >?</span>
        ) : issueCount > 0 ? (
          <span
            className={`shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded ${
              hasErrors ? 'bg-danger/15 text-danger' : 'bg-warn/15 text-warn'}`}
            data-severity={hasErrors ? 'error' : 'warning'}
            title={issueBadgeTitle(preflight?.errors ?? 0, preflight?.warnings ?? 0)}
          >{issueCount}</span>
        ) : undefined}
        onClick={() => toggle('issues')}
      />
      <SItem icon={<ListChecks size={15} />} label="Solve Queue"
        title="Queue saved projects to solve sequentially in the background; results persist to disk."
        active={activeSlidePanel === 'solveQueue'}
        rightEl={activeQueueCount > 0 ? (
          <span
            className="shrink-0 ml-1 text-[10px] font-mono font-semibold px-1.5 py-px rounded"
            style={{ background: 'rgba(217,119,6,0.18)', color: '#f59e0b' }}
          >{activeQueueCount}</span>
        ) : undefined}
        onClick={() => toggle('solveQueue')}
      />
    </div>
  )
}
