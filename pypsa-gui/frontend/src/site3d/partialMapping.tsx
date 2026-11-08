// SC-07 S5b R3 (UX condition 2, U-e, U-f; GK B N3, V5): a task whose model edit
// landed and whose 3D site mapping did not shows both, in words, on its card:
// "Done: <edit words>. Not done: the 3D site mapping (<reason>)." A step whose
// effect is uncertain (an apply with an unknown outcome) never reads "Not
// done": it keeps S5a's "Check the 3D site" path. "Prepare the mapping again"
// cancels the task and asks for a fresh mapping-only prepare, never a retry.

import { editLinesOf } from '../components/EditCard'

const SCENE_STEPS = new Set(['site_scene_prepare', 'site_scene_apply'])

/** The reason in plain words, from the refused apply's kind (never shown as a key): the three
 * typed apply refusals that prove nothing landed. A denied card never reaches the step (the
 * confirmation gate refuses before it begins), so the apply stays pending and the card's Next line
 * says so; no "Not done" is claimed for it (U-e). */
export const MAPPING_REASON: Record<string, string> = {
  proposal_expired: 'the prepared mapping expired',
  proposal_cancelled: 'it was cancelled',
  revision_conflict: 'the site changed after it was prepared',
}

type Obj = Record<string, unknown>
const object = (v: unknown): Obj | null => (v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : null)
const clause = (s: string) => s.trim().replace(/[.;:\s]+$/, '')

/** The line's parts, or null when the task is not a partial model-edit / mapping outcome. */
export function partialMapping(task: Obj): { done: string; reason: string } | null {
  // The edit's words with the panel's field labels (Guided's words, in both modes), from the
  // receipt rows the task view carries; the receipt's own `says` only when they are absent.
  const edits = Array.isArray(task.change_edits) ? task.change_edits : []
  const changes = (Array.isArray(task.changes) ? task.changes : []).flatMap((c, i) => {
    const lines = editLinesOf(edits[i])
    return lines.length ? lines : typeof c === 'string' && c.trim() ? [c] : []
  })
  const next = object(task.next_step)
  if (!changes.length || !next || !SCENE_STEPS.has(String(next.tool))) return null
  const kind = typeof next.error_kind === 'string' ? next.error_kind : null
  // A refused write step is stored "uncertain" (the task layer cannot know its effect). Only a
  // typed refusal that proves nothing landed (the reasons above) reads "Not done"; any other
  // uncertain step, scene_apply_outcome_unknown included, keeps S5a's check (U-f).
  const proven = next.status === 'failed' || (next.status === 'uncertain' && kind !== null && kind in MAPPING_REASON)
  if (!proven || kind === 'scene_apply_outcome_unknown') return null
  // The apply's words name a prepared mapping, so they never describe a failed prepare. A prepare
  // that asks for a choice fails with no kind (GK B H3).
  const reason = next.tool === 'site_scene_apply'
    ? (kind && MAPPING_REASON[kind]) || 'it did not complete'
    : kind ? 'it did not complete' : 'a target needs your choice'
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
