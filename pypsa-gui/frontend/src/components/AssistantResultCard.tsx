import { EditReceiptLine, editReceiptOf, setLine, type EditCardSet } from './EditCard'
import { formatElapsed, useElapsed } from '../hooks/useElapsed'
import { useUIStore } from '../store/uiStore'

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

export default function AssistantResultCard({ result, disabled, onRequest, continuation }: {
  result: unknown; disabled: boolean; onRequest: (text: string) => void
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
  if (!taskId && !link && !preview) return null
  return <div className="mt-1 rounded border border-border p-2 font-sans text-xs" data-testid="assistant-result-card">
    {taskId && <div data-testid="assistant-task-card">
      <div className="font-medium">{String(task?.title ?? 'Task')}</div>
      <div>{status} · {Number(task?.completed_steps ?? 0)} / {Number(task?.total_steps ?? 0)} steps</div>
      {next && <div>Next: {String(next.tool ?? '')} ({String(next.status ?? '')})</div>}
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
    {link && <a href={link} download={typeof data.filename === 'string' ? data.filename : undefined}
      className="text-accent hover:underline" data-testid="assistant-file-download">
      Download {typeof data.filename === 'string' ? data.filename : 'file'}
    </a>}
  </div>
}
