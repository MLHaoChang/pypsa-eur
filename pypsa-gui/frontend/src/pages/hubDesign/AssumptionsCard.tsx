import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'
import { ackFlowItems, type FlowItem, type ScopeCardId } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { openFlowTargetInPlace } from '../../layout/flowNavigation'
import { useFieldLabels } from '../../hooks/useFieldLabels'
import { fieldWords, isOpenItem } from '../../utils/flowItems'

// One card for every default the project still assumes (LC-46): the value and
// where it comes from, one "Keep all defaults" and an edit per row. Assumed
// values stay marked "assumed" until accepted. Inputs only the user can give
// are never here: they are required fields, not defaults.

/** The bulk button's words. The chat workflows and the not-via-chat reason use the
 *  same words; `backend/tests/test_assumptions_wording_parity.py` fails when they drift. */
export const KEEP_ALL_DEFAULTS = 'Keep all defaults'

const ASSUMED_HINT = /\s*[—:]\s*confirm it[^.]*\.?$/

/** A flow item the server raised for a template default the user has not confirmed. */
export const isAssumedItem = (it: FlowItem): boolean =>
  it.severity === 'warning' && it.code.startsWith('assumed_')

/** The card's rows: the open items (the one filter the Readiness count and the
 *  Expert badge use) that are assumed defaults. */
export const assumedItems = (items: FlowItem[]): FlowItem[] => items.filter(i => isOpenItem(i) && isAssumedItem(i))

/** The open card's plain headings, by the work card a default belongs to
 *  (spec §2: grid connection, units and sizing, reliability goal, costs). */
export const ASSUMPTION_GROUPS: Record<ScopeCardId, string> = {
  capacity: 'Units and sizing',
  grid: 'Grid connection',
  control: 'Control',
  reliability: 'Reliability goal',
  commercial: 'Costs and finance',
  handoff: 'Hand-off',
}
/** A default no work card claims: the design itself. */
export const DESIGN_GROUP = 'The design'

/** Rows under their heading, in first-seen order. Display only: the ack sends
 *  `items` as given, whatever the grouping. */
export function assumptionGroups(items: FlowItem[]): { title: string; rows: FlowItem[] }[] {
  const groups = new Map<string, FlowItem[]>()
  for (const it of items) {
    const title = (it.card && ASSUMPTION_GROUPS[it.card]) || DESIGN_GROUP
    groups.set(title, [...(groups.get(title) ?? []), it])
  }
  return [...groups].map(([title, rows]) => ({ title, rows }))
}

/** Open or closed is a per-user convenience in this browser, never project state. */
export const ASSUMPTIONS_OPEN_KEY = 'pypsa-gui:assumptions-open'
function readOpen(): boolean {
  try { return localStorage.getItem(ASSUMPTIONS_OPEN_KEY) === '1' } catch { return false }
}
function writeOpen(open: boolean): void {
  try { localStorage.setItem(ASSUMPTIONS_OPEN_KEY, open ? '1' : '0') } catch { /* no storage: this visit only */ }
}

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
        className="shrink-0 text-[11px] text-link hover:underline">
        Edit ›
      </button>
    </li>
  )
}

/** `waiting`: the section's state waits only on these defaults. Collapsed or
 *  open, "Keep all defaults" sends the same rows (guided-results 03, GK (5)). */
export function AssumptionsCard({ items, waiting = false }: { items: FlowItem[]; waiting?: boolean }) {
  const project = useUIStore(s => s.currentProject)
  const [open, setOpen] = useState(readOpen)
  const toggle = () => { setOpen(!open); writeOpen(!open) }
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
          <button type="button" data-testid="assumptions-toggle" aria-expanded={open} aria-controls="assumptions-rows"
            onClick={toggle} className="text-left hover:text-accent">
            {open ? '▾' : '▸'} Assumed <span className="font-normal text-muted">· {items.length} not yet confirmed</span>
          </button>
        </h3>
        <button type="button" data-testid="assumptions-accept-all"
          disabled={!project || accept.isPending}
          onClick={() => accept.mutate()}
          className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[11px] text-accent hover:border-accent disabled:opacity-50">
          <CheckCircle2 size={12} /> {KEEP_ALL_DEFAULTS}
        </button>
      </div>
      {waiting && (
        <p data-testid="assumptions-waiting" className="text-[11px] text-warn">
          This section waits on these {items.length === 1 ? 'default' : `${items.length} defaults`}: keep {items.length === 1 ? 'it' : 'them'}, or edit one.
        </p>
      )}
      {open && (
        <div id="assumptions-rows" className="flex flex-col gap-2">
          {assumptionGroups(items).map((g, gi, all) => (
            <div key={g.title} data-testid="assumption-group">
              {/* One group needs no heading: the card's own line names it. */}
              {all.length > 1 && <p className="pb-0.5 text-[11px] font-medium text-muted">{g.title}</p>}
              <ul className="flex flex-col gap-1.5">
                {g.rows.map((it, i) => <AssumedRow key={`${it.code}-${it.target.entity ?? ''}-${gi}-${i}`} item={it} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
      {accept.isError && (
        <p role="status" data-testid="assumptions-error" className="text-[11px] text-danger">
          The defaults could not be saved{reason ? `: ${reason}` : ''}. Try again.
        </p>
      )}
    </section>
  )
}
