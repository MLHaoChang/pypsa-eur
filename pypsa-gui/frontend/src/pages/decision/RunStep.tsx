// Run (spec §3 screen 7, plan S8): the budget in plain words — the whole
// estimate is charged when the run starts, so a stop still counts it (gate S4
// [N]) — the stage list, and the stop. A refusal is shown by its typed code;
// `RunRefused` codes are not manifest error kinds (gate S6 [N6]), their copy
// lives in `decisionVocabulary.ERROR_COPY`.
import type { DecisionStudy, RunRecord, StudyError } from '../../api/decisionStudies'
import { FIDELITY_LABELS, OPTION_LABELS, RUN_STATUS_LABELS, STAGE_LABELS, VOCAB, budgetSentence } from '../../utils/decisionVocabulary'
import { optionsFor } from './decisionModel'
import { Banner, Button, Card, Refusal } from './DecisionUi'

export default function RunStep({ study, run, error, onStart, onAbort, busy }: {
  study: DecisionStudy
  run: RunRecord | null
  error: StudyError | null
  onStart: () => void
  onAbort: () => void
  busy: boolean
}) {
  const running = run?.status === 'running'
  // Gate S8 [N1]: the sentence prices the NEXT run (the current answers), not the last one.
  const options = running ? run!.options : optionsFor(study.intake)
  const solves = options.length
  const stage = (id: string) => {
    if (!run) return STAGE_LABELS.not_run
    if (run.solved.includes(id)) return STAGE_LABELS.solved
    if (run.current === id) return STAGE_LABELS.solving
    if (run.pending.includes(id)) return running ? STAGE_LABELS.waiting : STAGE_LABELS.not_reached
    return run.status === 'done' ? STAGE_LABELS.not_solved : STAGE_LABELS.not_reached
  }
  return (
    <div className="flex flex-col gap-4">
      <Card title={VOCAB.run.label} right={running
        ? <Button kind="danger" onClick={onAbort}>Stop the run</Button>
        : <Button kind="primary" onClick={onStart} disabled={busy}>{run ? 'Run again' : VOCAB.run.label}</Button>}>
        <p data-testid="budget-sentence">{budgetSentence(solves, options.length)}</p>
        <p className="text-muted">
          Fidelity: {FIDELITY_LABELS.quick_screen}. Each option is solved on its own copy of the network;
          your projects are never changed.
        </p>
        {error && <Refusal error={error} />}
      </Card>
      {run && (
        <Card title={`Status: ${RUN_STATUS_LABELS[run.status]}`}>
          <ul data-testid="run-stages" className="flex flex-col gap-1">
            {run.options.map(id => (
              <li key={id} data-option={id} className="flex justify-between">
                <span>{OPTION_LABELS[id] ?? id}</span><span className="text-muted">{stage(id)}</span>
              </li>
            ))}
          </ul>
          {run.status === 'failed' && run.error && (
            <Banner tone="danger" title="The run failed.">
              <span>{run.error}</span><span>Check your answers and assumptions, then run again.</span>
            </Banner>
          )}
          {run.status === 'aborted' && (
            <Banner tone="warn" title="The run was stopped.">
              <span>The options it did not reach are named on the verdict; the full estimate stays charged.</span>
            </Banner>
          )}
        </Card>
      )}
    </div>
  )
}
