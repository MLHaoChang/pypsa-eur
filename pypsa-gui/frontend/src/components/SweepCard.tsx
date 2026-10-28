/**
 * The sweep card (LC-17): what a chat sensitivity sweep shows before it runs.
 * Per case its name and every `asset · field · base → case · unit` row, then
 * how many solves approving it queues. The backend contract is
 * `harness/sweep_card.py`; the rows use the edit card's value words.
 */
import { EditCardTable, type EditCard, type EditCardRow } from './EditCard'

type Scalar = string | number | boolean | null

export interface SweepCardRow {
  component_class: string
  name: string
  field: string
  label: string
  base: Scalar
  case: Scalar
  unit: string
  display?: { base: number | null; case: number | null; unit: string }
}

export interface SweepCardCase {
  name: string
  /** The name was derived from the changes, not given. */
  derived: boolean
  /** A case saved earlier with these inputs: reused, solved again only when its solve is stale. */
  existing: boolean
  rows: SweepCardRow[]
}

export interface SweepCard {
  version: 1
  kind: 'sweep'
  baseline: { project_id: string; name: string }
  cases: SweepCardCase[]
  solves: number
  says: string
  digest: string
}

function rowOk(r: unknown): boolean {
  const o = r as Record<string, unknown> | null
  return !!o && typeof o === 'object' && typeof o.component_class === 'string' && typeof o.name === 'string'
    && typeof o.field === 'string' && typeof o.unit === 'string'
}

/** The card, or null when it arrived malformed (then the request is never approvable). */
export function sweepCardOf(value: unknown): SweepCard | null {
  const o = value as Record<string, unknown> | null
  if (!o || typeof o !== 'object' || o.version !== 1 || o.kind !== 'sweep' || typeof o.says !== 'string'
    || typeof o.digest !== 'string' || typeof o.solves !== 'number' || !Array.isArray(o.cases) || o.cases.length === 0
    || !o.cases.every(c => c && typeof c.name === 'string' && Array.isArray(c.rows) && c.rows.every(rowOk))) return null
  return o as unknown as SweepCard
}

/** Rows shown before the rest is summarised ("2 more cases change."); every row is still on the server card. */
export const SWEEP_ROW_CAP = 24

/** One case as an edit card, so the table is EditCard's own (base → case as old → new). */
function caseCard(card: SweepCard, c: SweepCardCase, rows: SweepCardRow[]): EditCard {
  return {
    version: 1, kind: 'sweep_case', tool: 'run_sensitivity_sweep', says: '', digest: card.digest, complete: true,
    rows: rows.map((r): EditCardRow => ({
      component_class: r.component_class, name: r.name, field: r.field, label: r.label, old: r.base, new: r.case,
      unit: r.unit, role: 'typed',
      ...(r.display ? { display: { old: r.display.base, new: r.display.case, unit: r.display.unit } } : {}),
    })),
  }
}

export function SweepCardTable({ card, guided }: { card: SweepCard; guided: boolean }) {
  let budget = SWEEP_ROW_CAP
  const shown: { c: SweepCardCase; rows: SweepCardRow[] }[] = []
  for (const c of card.cases) {
    if (budget <= 0) break
    shown.push({ c, rows: c.rows.slice(0, budget) })
    budget -= c.rows.length
  }
  const hiddenCases = card.cases.length - shown.length
  return (
    <div className="mb-2 text-xs" data-testid="chat-sweep-card">
      <div className="font-medium mb-1">
        {card.cases.length} sensitivity case{card.cases.length === 1 ? '' : 's'} from {card.baseline.name}
      </div>
      {shown.map(({ c, rows }) => (
        <div key={c.name} data-testid="chat-sweep-case">
          <EditCardTable card={caseCard(card, c, rows)} guided={guided} labels={{
            // The caption names the case once; no asset header above it.
            header: '', caption: c.existing ? `${c.name} \u00b7 existing case` : c.name,
            old: 'Base', new: 'Case', noUnit: '',
          }} />
          {rows.length < c.rows.length && <p className="mb-2 text-muted" data-testid="chat-sweep-more-rows">
            {c.rows.length - rows.length} more {c.rows.length - rows.length === 1 ? 'asset changes' : 'assets change'} in this case.
          </p>}
        </div>
      ))}
      {hiddenCases > 0 && <p className="mb-1 text-muted" data-testid="chat-sweep-more-cases">
        {hiddenCases} more {hiddenCases === 1 ? 'case changes' : 'cases change'}.
      </p>}
      <p className="text-muted" data-testid="chat-sweep-solves">
        {card.solves === 0 ? 'Queues no solve: every case is already solved.'
          : `Queues ${card.solves} solve${card.solves === 1 ? '' : 's'}.`} The baseline stays unchanged.
      </p>
    </div>
  )
}
