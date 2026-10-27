import { EditReceiptLine, editReceiptOf, setLine, type EditCardSet } from './EditCard'
import { formatElapsed, useElapsed } from '../hooks/useElapsed'
import { useUIStore } from '../store/uiStore'
import { openScenariosHighlight } from './uiNavigate'
import { CARD_GROUPS } from '../pages/results/groups'

/** LC-12: where a plan's waiting job stands in this view. */
export type ContinuationLine =
  | { state: 'waiting'; onStop: () => void }
  | { state: 'held'; onContinueNow: () => void }
  | { state: 'continued' | 'stopped' }
  | { state: 'elsewhere'; project: string }

/** Read task/artifact metadata from the existing tool_result stream. */
function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

/** LC-02: a plan step's state in words; an unknown state shows as stored. */
const STEP_WORDS: Record<string, string> = {
  pending: 'to do', running: 'running', waiting: 'waiting for a job', completed: 'done', failed: 'failed', uncertain: 'needs review',
}

/** LC-02: a plan's state in Guided's words; Expert shows the stored state. */
const TASK_WORDS: Record<string, string> = {
  ready: 'Ready', waiting_job: 'Waiting for a background job', paused_at_card: 'Paused before the next card',
  needs_review: 'Needs review', completed: 'Done', cancelled: 'Cancelled',
}

/** A brief's card in the results' words; the backend's 'hand-off' is the 'handoff' group. */
function cardLabel(card: unknown): string {
  const id = card === 'hand-off' ? 'handoff' : String(card ?? '')
  return CARD_GROUPS.find(g => g.id === id)?.label ?? id
}

/** LC-02: the plan's steps (the current phase's, with a brief), each with its state; the next step is marked. */
function PlanSteps({ task, next, guided, toolWords }: {
  task: Record<string, unknown>; next: Record<string, unknown> | null; guided: boolean; toolWords?: (tool: string) => string
}) {
  const steps = Array.isArray(task.steps) ? task.steps.map(object).filter((s): s is Record<string, unknown> =>
    s !== null && typeof s.tool === 'string' && typeof s.status === 'string') : []
  const brief = object(task.brief)
  const phase = object(brief?.phase)
  const changes = Array.isArray(task.changes) ? task.changes.filter((c): c is string => typeof c === 'string') : []
  return <>
    {brief && phase && <div data-testid="assistant-task-phase">
      {String(brief.objective ?? '')} · phase {Number(phase.index)} of {Number(phase.of)}: {cardLabel(phase.card)}
    </div>}
    {steps.length > 0 && <ol aria-label="Plan steps" className="my-1 list-decimal pl-5">
      {steps.map((s, i) => {
        const current = next !== null && next.id === s.id
        // Guided says what waits; Expert names the next step's job by its ID.
        const job = s.status === 'waiting' && current && !guided && typeof next.job_id === 'string' ? next.job_id : null
        const state = s.status === 'waiting' && guided ? 'waiting for a background job'
          : job ? `waiting for job ${job}` : STEP_WORDS[String(s.status)] ?? String(s.status)
        const tool = String(s.tool)
        return <li key={typeof s.id === 'string' ? s.id : i} data-testid="assistant-task-step" data-status={String(s.status)}
          aria-current={current ? 'step' : undefined} className={current ? 'font-medium' : 'text-muted'}>
          {toolWords ? toolWords(tool) : tool} · {state}
        </li>
      })}
    </ol>}
    {task.step_details_truncated === true && <p className="text-muted">Some steps are not listed.</p>}
    {changes.length > 0 && <div data-testid="assistant-task-applied">Applied:
      <ul className="list-disc pl-5">{changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
    </div>}
  </>
}

/** The waiting task's line: elapsed time and a way out, or why it is held. */
function WaitingLine({ line }: { line: ContinuationLine }) {
  const elapsed = useElapsed(line.state === 'waiting')
  if (line.state === 'waiting') return <div data-testid="task-continuation-line">
    Waiting for the solve to finish · {formatElapsed(elapsed)}{' '}
    <button type="button" className="text-muted hover:text-text" onClick={line.onStop}
      title="The solve runs on; the conversation will not continue by itself">Stop waiting</button>
  </div>
  if (line.state === 'held') return <div data-testid="task-continuation-line">
    The solve finished · I&apos;ll continue when you&apos;re ready{' '}
    <button type="button" className="text-accent" onClick={line.onContinueNow}>Continue now</button>
  </div>
  if (line.state === 'elsewhere') return <div data-testid="task-continuation-line">
    This plan&apos;s solve is in {line.project}: open it to continue
  </div>
  return <div data-testid="task-continuation-line" className="text-muted">
    {line.state === 'continued' ? 'The solve finished · continued below' : 'Not waiting for this solve; it runs on'}
  </div>
}

export default function AssistantResultCard({ result, disabled, onRequest, continuation, toolWords }: {
  result: unknown; disabled: boolean; onRequest: (text: string) => void
  /** LC-02: names a plan step's tool in words (Guided); the tool name otherwise. */
  toolWords?: (tool: string) => string
  /** LC-12: set while this card's plan waits on its job. */
  continuation?: ContinuationLine
}) {
  // Guided names a parameter in words (the edit card's label), Expert by its field.
  const guided = useUIStore((s) => s.uiMode) === 'guided'
  const data = object(result)
  if (!data) return null
  // Raw checked-mode cards cannot bypass answer/artifact admission. The current
  // pilot has no approved artifact/task-card contract.
  if (object(data._assurance)) return null
  const receipt = editReceiptOf(data._edit_receipt)
  if (receipt) return <EditReceiptLine receipt={receipt} disabled={disabled} onRequest={onRequest} guided={guided} />
  const task = object(data._task) ?? (typeof data.task_id === 'string' ? data : null)
  const taskId = typeof task?.task_id === 'string' && /^[0-9a-f-]{36}$/.test(task.task_id) ? task.task_id : null
  const status = typeof task?.status === 'string' ? task.status : ''
  const next = object(task?.next_step)
  const link = typeof data.download_url === 'string'
    && /^\/api\/projects\/[^/?#]+\/uploads\/[0-9a-f]{16}\/blob$/.test(data.download_url)
    ? data.download_url : null
  const preview = typeof data.preview_id === 'string' && data.valid === true && Array.isArray(data.diff) ? data : null
  // LC-17: a sweep's cases, opened in Scenarios on the user's click (never automatically).
  const sweepCases = typeof data.baseline_project_id === 'string' && Array.isArray(data.cases)
    ? data.cases.map(object).map(c => c?.project_id).filter((id): id is string => typeof id === 'string').slice(0, 4) : []
  if (!taskId && !link && !preview && !sweepCases.length) return null
  return <div className="mt-1 rounded border border-border p-2 font-sans text-xs" data-testid="assistant-result-card">
    {taskId && <div data-testid="assistant-task-card">
      <div className="font-medium">{String(task?.title ?? 'Task')}</div>
      <div>{(guided && TASK_WORDS[status]) || status} · {Number(task?.completed_steps ?? 0)} / {Number(task?.total_steps ?? 0)} steps</div>
      {next && (guided
        ? <div>Next: {toolWords ? toolWords(String(next.tool ?? '')) : String(next.tool ?? '')} ({
          next.status === 'waiting' ? 'waiting for a background job' : STEP_WORDS[String(next.status)] ?? String(next.status ?? '')})</div>
        : <div>Next: {String(next.tool ?? '')} ({String(next.status ?? '')})</div>)}
      <PlanSteps task={task!} next={next} guided={guided} toolWords={toolWords} />
      {status === 'waiting_job' && continuation && <WaitingLine line={continuation} />}
      {status === 'needs_review' && <p>Verify the previous action before retrying. Its effects may already exist.</p>}
      <div className="mt-1 flex gap-2">
        {status !== 'completed' && status !== 'cancelled' && <>
          {status !== 'waiting_job' && <button type="button" disabled={disabled} className="text-accent disabled:opacity-40"
            onClick={() => onRequest(status === 'needs_review'
              ? `Use get_task with task_id ${taskId}. Inspect the uncertain step's effects and explain what needs verification before reconciliation.`
              : `Use resume_task with task_id ${taskId}, then execute the next offered tool call. Preserve all confirmations and stop if a job is still pending.`)}>
            {status === 'needs_review' ? 'Review task' : 'Resume task'}
          </button>}
          <button type="button" disabled={disabled} className="text-muted disabled:opacity-40"
            onClick={() => onRequest(`Use cancel_task with task_id ${taskId}. Stop future steps; leave running jobs alone.`)}>Cancel task</button>
        </>}
      </div>
      <details className="mt-1 text-muted"><summary>Task ID</summary>{taskId}</details>
    </div>}
    {preview && <div data-testid="assistant-change-preview">
      <div className="font-medium">Project change preview · {Number(preview.changes_total ?? 0)} values</div>
      {Array.isArray(preview.sets) && (preview.sets as EditCardSet[])
        .filter(g => object(g) && Array.isArray(g.names) && typeof g.words === 'string').map((g, i) => {
        const diff = (preview.diff as unknown[]).map(object).find(d => d && d.component_class === g.component_class
          && d.attribute === g.attribute && g.names.includes(String(d.name)))
        const value = diff ? `\u2192 ${String(diff.after ?? '')}${diff.unit ? ` ${String(diff.unit)}` : ''}` : ''
        return <div key={i} data-testid="assistant-change-set">
          {setLine(g, guided, diff && typeof diff.label === 'string' ? diff.label : undefined, value)}</div>
      })}
      <div className="overflow-x-auto">
        <table aria-label="Project change preview" className="w-full text-left">
          <thead><tr><th>Asset</th><th>Parameter</th><th>Before</th><th>After</th><th>Unit</th></tr></thead>
          <tbody>{(preview.diff as unknown[]).map((row, index) => {
            const diff = object(row)
            return diff ? <tr key={index}>
              <td>{String(diff.name ?? '')}</td><td>{String((guided && diff.label) || diff.attribute || '')}</td>
              <td>{String(diff.before ?? 'not set')}</td><td>{String(diff.after ?? '')}</td><td>{String(diff.unit ?? '')}</td>
            </tr> : null
          })}</tbody>
        </table>
      </div>
      {preview.diff_truncated === true && <p>Some change details are omitted. Review the full requested changes before applying.</p>}
      <p className="text-muted">No network changes applied. Applying requires confirmation.</p>
    </div>}
    {next && typeof next.error === 'string' && <p className="text-amber-400">{next.error}</p>}
    {sweepCases.length > 0 && <button type="button" className="text-accent hover:underline" data-testid="assistant-sweep-open"
      onClick={() => openScenariosHighlight(sweepCases)}>
      Open the new case{sweepCases.length === 1 ? '' : 's'} in Scenarios ›
    </button>}
    {link && <a href={link} download={typeof data.filename === 'string' ? data.filename : undefined}
      className="text-accent hover:underline" data-testid="assistant-file-download">
      Download {typeof data.filename === 'string' ? data.filename : 'file'}
    </a>}
  </div>
}
