import { Play } from 'lucide-react'
import { useRunGate } from '../hooks/useRunGate'
import { useUIStore } from '../store/uiStore'
import { useVerdictStudyEntry } from '../hooks/useVerdictStudyEntry'

/** The Run in section 9 (Readiness and run). Not a second Run: it asks the
 *  header's gated Run to start (`requestRunSolve`), so the same gate, save and
 *  queue apply. Shown only when the gate lets a run through; a blocked project
 *  keeps the header's reason and the findings below. */
export function RunFromReadiness({ testId = 'readiness-run' }: { testId?: string }) {
  // The same gate as the header Run it asks to start.
  const { hasStudy } = useVerdictStudyEntry()
  const uiMode = useUIStore(s => s.uiMode)
  const gate = useRunGate(hasStudy && uiMode === 'expert' ? 'all' : 'optimisation')
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  if (gate.blocked || gate.checking) return null
  return (
    <button type="button" data-testid={testId} onClick={requestRunSolve}
      className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white">
      <Play size={12} /> Run
    </button>
  )
}
