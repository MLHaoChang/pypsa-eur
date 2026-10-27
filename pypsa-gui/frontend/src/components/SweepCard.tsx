/**
 * The sweep card (LC-17): what a chat sensitivity sweep shows before it runs.
 * Per case its name and every `asset · field · base → case · unit` row, then
 * how many solves approving it queues. The backend contract is
 * `harness/sweep_card.py`; the rows use the edit card's value words.
 */
import { assetWords, cellValue, showDisplay } from './EditCard'

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

export function SweepCardTable({ card, guided }: { card: SweepCard; guided: boolean }) {
  return (
    <div className="mb-2 text-xs" data-testid="chat-sweep-card">
      <div className="font-medium mb-1">
        {card.cases.length} sensitivity case{card.cases.length === 1 ? '' : 's'} from {card.baseline.name}
      </div>
      {card.cases.map(c => (
        <div key={c.name} className="mb-2 overflow-x-auto" data-testid="chat-sweep-case">
          <table className="w-full text-left border-collapse">
            <caption className="text-left font-semibold mb-1">
              {c.name}
              {c.existing && <span className="ml-1 font-normal text-muted">(existing case)</span>}
            </caption>
            <thead><tr>{['Asset', 'Field', 'Base', 'Case', 'Unit'].map(h => (
              <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>
            ))}</tr></thead>
            <tbody>{c.rows.map((r, i) => (
              <tr key={i} data-testid="chat-sweep-row">
                <td className="p-1 border-b border-border break-words">{assetWords(r.component_class, r.name)}</td>
                <td className="p-1 border-b border-border break-words">{guided ? r.label : r.field}</td>
                {r.display ? <>
                  <td className="p-1 border-b border-border">{showDisplay(r.display.base)}</td>
                  <td className="p-1 border-b border-border">{showDisplay(r.display.case)}</td>
                  <td className="p-1 border-b border-border">{r.display.unit}</td>
                </> : <>
                  <td className="p-1 border-b border-border">{cellValue(r.base)}</td>
                  <td className="p-1 border-b border-border">{cellValue(r.case)}</td>
                  <td className="p-1 border-b border-border">{r.unit || 'No unit'}</td>
                </>}
              </tr>
            ))}</tbody>
          </table>
        </div>
      ))}
      <p className="text-muted" data-testid="chat-sweep-solves">
        {card.solves === 0 ? 'Queues no solve: every case is already solved.'
          : `Queues ${card.solves} solve${card.solves === 1 ? '' : 's'}.`} The baseline stays unchanged.
      </p>
    </div>
  )
}
