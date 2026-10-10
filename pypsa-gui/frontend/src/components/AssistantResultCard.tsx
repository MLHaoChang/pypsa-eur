/** Read task/artifact metadata from the existing tool_result stream. */
function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

export default function AssistantResultCard({ result, disabled, onRequest }: {
  result: unknown; disabled: boolean; onRequest: (text: string) => void
}) {
  const data = object(result)
  if (!data) return null
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
      {status === 'needs_review' && <p>Verify the previous action before retrying. Its effects may already exist.</p>}
      <div className="mt-1 flex gap-2">
        {status !== 'completed' && status !== 'cancelled' && <>
          <button type="button" disabled={disabled} className="text-accent disabled:opacity-40"
            onClick={() => onRequest(status === 'needs_review'
              ? `Use get_task with task_id ${taskId}. Inspect the uncertain step's effects and explain what needs verification before reconciliation.`
              : `Use resume_task with task_id ${taskId}, then execute the next offered tool call. Preserve all confirmations and stop if a job is still pending.`)}>
            {status === 'needs_review' ? 'Review task' : 'Resume task'}
          </button>
          <button type="button" disabled={disabled} className="text-muted disabled:opacity-40"
            onClick={() => onRequest(`Use cancel_task with task_id ${taskId}. Stop future steps; leave running jobs alone.`)}>Cancel task</button>
        </>}
      </div>
      <details className="mt-1 text-muted"><summary>Task ID</summary>{taskId}</details>
    </div>}
    {preview && <div data-testid="assistant-change-preview">
      <div className="font-medium">Project change preview · {Number(preview.changes_total ?? 0)} values</div>
      <div className="overflow-x-auto">
        <table aria-label="Project change preview" className="w-full text-left">
          <thead><tr><th>Asset</th><th>Parameter</th><th>Before</th><th>After</th><th>Unit</th></tr></thead>
          <tbody>{(preview.diff as unknown[]).map((row, index) => {
            const diff = object(row)
            return diff ? <tr key={index}>
              <td>{String(diff.name ?? '')}</td><td>{String(diff.attribute ?? '')}</td>
              <td>{String(diff.before ?? '')}</td><td>{String(diff.after ?? '')}</td><td>{String(diff.unit ?? '')}</td>
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
