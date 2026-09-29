// Step 3 — Goal (guided-mode spec §5.3): how much shortfall per year is
// acceptable, how strict to be on energy (advanced), the price of undelivered
// energy (read-only, with an assistant fix), then Run — the Expert panel's
// own `buildEhStudyBody`, so both views start the same study.
//
// Running / failed / aborted are read from the study record (P24-BE gate
// N2), and the running line is this card's own text — never the review
// route's chat-oriented message (N3).
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Play, Square } from 'lucide-react'
import { resultsApi } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { blockerMessage } from '../../../utils/blockerMessage'
import { nk } from '../../../utils/queryKeys'
import { buildEhStudyBody, ehStudyQueryKeys } from '../../results/EhReferenceDesignPanel'
import { useHubDesignStore } from '../hubDesignStore'
import { VOLL_TEXT } from '../delegate'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { Term } from '../shared/Term'
import {
  templateForm, useHubReadiness, useHubSolverConfig, useHubStudy, useHubTemplate,
} from '../useHubData'

export function GoalCard() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const { study, running, isPending: studyPending } = useHubStudy()
  const { template, isPending: templatePending, isError: templateError,
    refetch: refetchTemplate } = useHubTemplate()
  const { readiness } = useHubReadiness(template, running, !studyPending && !templatePending)
  const liveStudy = useLiveStudyRunning()
  const { data: solverConfig } = useHubSolverConfig()
  const archetype = useHubDesignStore(s => s.archetype)
  const loleTarget = useHubDesignStore(s => s.loleTarget)
  const ensCap = useHubDesignStore(s => s.ensCap)
  const setLoleTarget = useHubDesignStore(s => s.setLoleTarget)
  const setEnsCap = useHubDesignStore(s => s.setEnsCap)
  const [advanced, setAdvanced] = useState(false)
  const [blocked, setBlocked] = useState<string | null>(null)

  // Default goal: the template's own override (set on reset), else the pack
  // default readiness reports for the chosen site type. A pack-seeded value
  // follows the pack; the user's typing is never overwritten.
  const packLole = readiness?.pack_defaults?.target_lole_h
  useEffect(() => {
    if (packLole === undefined) return
    const s = useHubDesignStore.getState()
    if (s.loleSource === 'template' || s.loleSource === 'user') return
    const v = packLole == null ? '' : String(packLole)
    if (s.loleTarget !== v || s.loleSource !== 'pack') s.seedLoleFromPack(v)
  }, [packLole])

  const form = { ...templateForm(template), loleTarget, ensCap }
  const built = buildEhStudyBody(archetype, form)
  const voll = typeof solverConfig?.voll === 'number' ? solverConfig.voll : null
  const vollMissing = voll != null && !(voll > 0)

  const run = useMutation({
    mutationFn: () => resultsApi.startEhStudy(built.body!),
    onMutate: () => setBlocked(null),
    onSuccess: () => {
      for (const key of ehStudyQueryKeys(project)) void qc.invalidateQueries({ queryKey: key })
    },
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })
  const abort = useMutation({
    mutationFn: () => resultsApi.abortEhStudy(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: nk(project, 'results', 'eh_study') }),
  })

  const status = study?.status
  const again = status === 'failed' || status === 'aborted' || status === 'done'
  const pipeline = study?.report?.pipeline
  // Plain words, and a count only when the record has one — never "0 of N".
  const budget = pipeline?.budget_solves ?? study?.budget_solves
  const runningText = pipeline?.budget_solves && typeof pipeline.solves_consumed === 'number'
    ? `Studying… ${pipeline.solves_consumed} of ${pipeline.budget_solves} calculation steps`
    : budget ? `Studying… (up to ${budget} calculation steps)` : 'Studying…'
  const problem = built.error ?? blocked
  // P24-FE re-gate note 2: without the template the card would silently run
  // the default site type's study instead of the template's recommended one.
  const templateUnknown = templateError && !template
  const ensDefault = readiness?.pack_defaults?.ens_cap_permyriad

  return (
    <CardShell step="goal" testId="hub-card-goal" title="Goal">
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-semibold text-text">
          <Term k="shortfall_hours">Allowed shortfall (hours per year)</Term>
        </span>
        <input type="number" step="any" min={0} data-testid="hub-goal-lole"
          value={loleTarget} disabled={running}
          placeholder="e.g. 3"
          onChange={e => setLoleTarget(e.target.value)}
          className="w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text" />
        {loleTarget.trim() === '' && (
          <span className="text-muted" data-testid="hub-goal-empty-hint">
            No goal yet. Type how many hours per year without power you can accept (for example 3)
            to get a pass or fail answer.
          </span>
        )}
      </label>

      <div className="flex flex-col gap-2">
        <button type="button" data-testid="hub-goal-advanced" aria-expanded={advanced}
          onClick={() => setAdvanced(a => !a)}
          className="self-start text-[11px] text-muted hover:text-accent">
          {advanced ? '▾' : '▸'} More settings
        </button>
        {advanced && (
          <label className="flex flex-col gap-1 text-[12px]">
            <span className="font-semibold text-text">
              <Term k="energy_strictness">Energy strictness (parts per 10 000 of yearly energy)</Term>
            </span>
            <input type="number" step="any" min={0} data-testid="hub-goal-ens"
              value={ensCap} disabled={running}
              placeholder={ensDefault != null ? `${ensDefault} (default)` : 'default'}
              onChange={e => setEnsCap(e.target.value)}
              className="w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text" />
          </label>
        )}
      </div>

      <div data-testid="hub-goal-voll" className="flex flex-wrap items-center gap-2 text-[12px]">
        <span><Term k="voll_plain">Price of undelivered energy</Term>:</span>
        {voll == null ? <span className="text-muted">…</span>
          : vollMissing ? (
            <>
              <span className="text-warn">not set — the study needs a price above zero</span>
              <DelegateButton testId="hub-goal-voll-fix" text={VOLL_TEXT}
                label="Let the assistant set it"
                disabled={liveStudy} disabledTitle={LIVE_STUDY_EDIT} />
            </>
          ) : <span className="text-text">€{voll.toLocaleString('en-US')} per MWh</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="hub-goal-run"
          onClick={() => run.mutate()}
          disabled={running || run.isPending || built.error !== null || vollMissing || !project
            || templateUnknown}
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {running ? 'Studying…' : again ? 'Run again' : 'Run study'}
        </button>
        {running && (
          <>
            <span data-testid="hub-goal-running" className="text-[12px] text-muted">{runningText}</span>
            <button type="button" data-testid="hub-goal-abort" onClick={() => abort.mutate()}
              title="Stops at the next stage boundary; the network is left as it was."
              className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-danger hover:text-danger">
              <Square size={10} /> Abort
            </button>
          </>
        )}
      </div>

      {templateUnknown && (
        <p data-testid="hub-goal-template-error" className="flex flex-wrap items-center gap-2 text-[12px] text-warn">
          This project's recommended settings could not be read, so the study cannot start
          with them yet.
          <button type="button" data-testid="hub-goal-template-retry"
            onClick={() => void refetchTemplate()}
            className="rounded border border-warn/60 px-2 py-0.5 text-[11px] hover:bg-warn/10">
            Retry
          </button>
        </p>
      )}
      {problem && (
        <p data-testid="hub-goal-blocked" className="text-[12px] text-warn">{problem}</p>
      )}
      {!running && status === 'failed' && (
        <p data-testid="hub-goal-error" className="text-[12px] text-danger">
          The last study failed: {study?.error ?? 'no reason was given'}
        </p>
      )}
      {!running && status === 'aborted' && (
        <p data-testid="hub-goal-error" className="text-[12px] text-warn">
          Stopped before the end — the results cover only the parts that finished.
        </p>
      )}
    </CardShell>
  )
}
