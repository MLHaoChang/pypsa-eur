import { Lock } from 'lucide-react'
import { useUIStore } from '../../store/uiStore'
import { useProjectRun } from '../../hooks/useProjectRun'
import { RUN_READ_ONLY_LINE } from '../../utils/mutationGuard'
import { RunStatusLine } from './RunStatusLine'

/**
 * The run strip at the top of the tab, above the header (run progress spec §1
 * and §4, UX plan GO (a) and 4). While a run of the project on screen is
 * queued or going on, one full-width line says what runs and how far it is; while the project is
 * read-only because it is being solved, the same line says so once
 * (owner decision RUN-READONLY-1). It takes the place of the read-only banner
 * for that reason (`LockBanner` yields), so there is one line, not two.
 * Renders nothing otherwise.
 */
export default function RunStrip() {
  const run = useProjectRun()
  const solving = useUIStore(s => s.readOnly && s.readOnlyReason === 'solving')
  // The live region stays mounted, empty, while nothing runs: a screen reader
  // reliably announces text that arrives in an existing region, not one that
  // is mounted already filled, so the first stage is heard too (UX GO (a)).
  const announce = <span className="sr-only" role="status">{run ? `${run.name}: ${run.stage}` : ''}</span>
  return (
    <>
    {announce}
    {(run || solving) && (
    <div data-testid="run-strip"
      className="flex items-center gap-3 px-3 h-8 shrink-0 overflow-hidden border-b border-warn/30 bg-warn/10 text-[11.5px] text-warn">
      {run && <RunStatusLine run={run} />}
      {solving && (
        <span data-testid="run-strip-readonly" className="ml-auto flex shrink-0 items-center gap-1.5 text-warn/80">
          <Lock size={12} className="shrink-0" aria-hidden />
          {RUN_READ_ONLY_LINE}
        </span>
      )}
    </div>
    )}
    </>
  )
}
