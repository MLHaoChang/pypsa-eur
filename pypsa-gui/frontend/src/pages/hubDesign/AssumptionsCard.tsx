import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'
import { ackFlowItems, type FlowItem } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { openFlowTargetInPlace } from '../../layout/flowNavigation'
import { useFieldLabels } from '../../hooks/useFieldLabels'
import { fieldWords } from '../../utils/flowItems'

// One card for every default the project still assumes (LC-46): the value and
// where it comes from, one "Keep all defaults" and an edit per row. Assumed
// values stay marked "assumed" until accepted. Inputs only the user can give
// are never here: they are required fields, not defaults.

/** The bulk button's words. The chat workflows and the not-via-chat reason use the
 *  same words; `backend/tests/test_assumptions_wording_parity.py` fails when they drift. */
export const KEEP_ALL_DEFAULTS = 'Keep all defaults'

const ASSUMED_HINT = /\s*—\s*confirm it[^.]*\.?$/

/** A flow item the server raised for a template default the user has not confirmed. */
export const isAssumedItem = (it: FlowItem): boolean =>
  it.severity === 'warning' && it.code.startsWith('assumed_')

/** The server's own reason (`detail.message`), when it sent one. */
function failureReason(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  const msg = typeof detail === 'string' ? detail : (detail as { message?: unknown } | undefined)?.message
  return typeof msg === 'string' ? msg.replace(/[.\s]+$/, '') : ''
}

function AssumedRow({ item }: { item: FlowItem }) {
  const labels = useFieldLabels()
  const field = fieldWords(item.target, labels, item.message)
  return (
    <li data-testid="assumption-row" className="flex items-start gap-2 text-[12px] text-text">
      <span className="flex-1">{item.message.replace(ASSUMED_HINT, '')}</span>
      <button type="button" data-testid="assumption-edit"
        onClick={() => openFlowTargetInPlace(item.target)}
        title={`Edit ${field ?? 'this value'} here`}
        className="shrink-0 text-[11px] text-accent hover:underline">
        Edit ›
      </button>
    </li>
  )
}

export function AssumptionsCard({ items }: { items: FlowItem[] }) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const accept = useMutation({
    mutationFn: () => ackFlowItems(project as string,
      items.map(i => ({ code: i.code, entity: i.target.entity }))),
    onSettled: () => { void qc.invalidateQueries({ queryKey: nk(project, 'flow') }) },
  })
  const reason = accept.isError ? failureReason(accept.error) : ''
  if (items.length === 0) {
    // After "Keep all defaults" succeeds the flow re-reads and the rows leave: one muted line, until the next visit.
    return accept.isSuccess
      ? <p role="status" data-testid="assumptions-kept" className="text-[11px] text-muted">Defaults kept</p>
      : null
  }
  return (
    <section data-testid="assumptions-card" aria-label="Assumptions"
      className="flex flex-col gap-2 rounded border border-border bg-panel px-3 py-2">
      <div className="flex items-center gap-2">
        <h3 className="flex-1 text-[12px] font-semibold text-text">
          Assumptions <span className="font-normal text-muted">· {items.length} default{items.length === 1 ? '' : 's'} not yet confirmed</span>
        </h3>
        <button type="button" data-testid="assumptions-accept-all"
          disabled={!project || accept.isPending}
          onClick={() => accept.mutate()}
          className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[11px] text-accent hover:border-accent disabled:opacity-50">
          <CheckCircle2 size={12} /> {KEEP_ALL_DEFAULTS}
        </button>
      </div>
      <ul className="flex flex-col gap-1.5">
        {items.map((it, i) => <AssumedRow key={`${it.code}-${it.target.entity ?? ''}-${i}`} item={it} />)}
      </ul>
      {accept.isError && (
        <p role="status" data-testid="assumptions-error" className="text-[11px] text-danger">
          The defaults could not be saved{reason ? `: ${reason}` : ''}. Try again.
        </p>
      )}
    </section>
  )
}
