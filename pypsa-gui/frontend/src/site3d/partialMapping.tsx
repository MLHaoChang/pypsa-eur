// SC-07 S5b R3 (UX condition 2, U-e, U-f; GK B N3, V5): a task whose model edit
// landed and whose 3D site mapping did not shows both, in words, on its card:
// "Done: <edit words>. Not done: the 3D site mapping (<reason>)." A step whose
// effect is uncertain (an apply with an unknown outcome) never reads "Not
// done": it keeps S5a's "Check the 3D site" path. "Prepare the mapping again"
// cancels the task and asks for a fresh mapping-only prepare, never a retry.

const SCENE_STEPS = new Set(['site_scene_prepare', 'site_scene_apply'])

/** The reason in plain words, from the failed step's kind (never shown as a key). */
export const MAPPING_REASON: Record<string, string> = {
  proposal_expired: 'the prepared mapping expired',
  proposal_cancelled: 'it was cancelled',
  revision_conflict: 'the site changed after it was prepared',
  confirmation_denied: 'you did not approve it',
  unknown_target: 'a target needs your choice',
}

type Obj = Record<string, unknown>
const object = (v: unknown): Obj | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : null)
const clause = (s: string) => s.trim().replace(/[.;:\s]+$/, '')

/** The line's parts, or null when the task is not a partial model-edit / mapping outcome. */
export function partialMapping(task: Obj): { done: string; reason: string } | null {
  const changes = Array.isArray(task.changes) ? task.changes.filter((c): c is string => typeof c === 'string' && !!c.trim()) : []
  const next = object(task.next_step)
  if (!changes.length || !next || !SCENE_STEPS.has(String(next.tool)) || next.status !== 'failed') return null
  const kind = typeof next.error_kind === 'string' ? next.error_kind : null
  if (kind === 'scene_apply_outcome_unknown') return null
  // A prepare that asks for a choice fails with no kind (GK B H3).
  const reason = (kind && MAPPING_REASON[kind]) ?? (next.tool === 'site_scene_prepare' && !kind
    ? 'a target needs your choice' : 'it did not complete')
  return { done: changes.map(clause).join('. '), reason }
}

export function PartialMappingLine({ task, taskId, disabled, onRequest }: {
  task: Obj; taskId: string; disabled?: boolean
  onRequest: (text: string, label?: string) => void
}) {
  const partial = partialMapping(task)
  if (!partial) return null
  return (
    <div className="mt-1" data-testid="task-partial-mapping">
      <p role="status">Done: {partial.done}. Not done: the 3D site mapping ({partial.reason}).</p>
      {/* A button, no "›": it asks here and goes nowhere (UX condition 2). */}
      <button type="button" disabled={disabled} className="text-accent disabled:opacity-40" data-testid="task-prepare-mapping-again"
        onClick={() => onRequest(`Use cancel_task with task_id ${taskId}. Then prepare only the 3D site mapping again, `
          + 'as a new request with site_scene_prepare for the same targets, and show its card. Do not repeat the model edit, '
          + 'and do not use resolve_task_step retry.', 'Prepare the mapping again')}>
        Prepare the mapping again
      </button>
    </div>
  )
}
