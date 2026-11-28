// Issue template-e2e 20: what the sizing payload leaves out of installed
// capacity (the grid connection, a product's buy-in or shortfall, a grid
// supply kept in the total with its reason), as the report's sizing table
// prints it (`services/reports/evidence._t_sizing`, the rows after Total).
// It reads only the payload's fields: no p_nom, no filter of its own
// (GK A 33 S1).

/** The connection fields and product slacks of `sections.sizing.payload`
 *  (`eh_report.sizing_summary_from_network`). */
export interface EhProductSlack {
  name: string
  process_role: string
  carrier?: string | null
  p_nom?: number | null
  energy?: number | null
  unit?: string | null
  energy_unit?: string | null
}

export interface EhSizingConnection {
  grid_connection_mw?: number | null
  grid_connection_refusal?: string | null
  grid_import_peak_mw?: number | null
  grid_import_peak_by_period?: Record<string, number> | null
  connection_check?: 'within' | 'exceeds' | 'unbounded' | null
  grid_supply_counted?: string[] | null
  product_slacks?: EhProductSlack[] | null
  /** The span the slacks' energy is over, in words ("2027–2035", "8,760 h"). */
  horizon?: string | null
}

/** `eh_report.CONNECTION_TOL_MW`: the peak may sit this far above the limit
 *  and still read "within" (pinned on both sides by the parity fixture). */
export const CONNECTION_TOL_MW = 1e-6

export const GRID_ROW = 'Grid connection (import limit, not counted)'

/** `formatting.fmt_number(v, unit, digits=1)`: Python's `f"{v:,.1f}"`.
 *  Python rounds the exact binary value, as `toFixed` does (`toLocaleString`
 *  rounds the shortest decimal, so 0.15 would read "0.2", Python's "0.1"),
 *  but an exact tie (always a quarter at one digit) goes to even ("32.25" →
 *  "32.2") where `toFixed` rounds it up. Every payload figure is a float
 *  (`_p_nom`, `import_capacity`, the peaks), so Python's int branch never
 *  applies. */
export function fmt1(v: number | null | undefined, unit?: string | null): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  let x = v
  if (Number.isInteger(v * 4) && !Number.isInteger(v * 2)) {
    const lo = Math.floor(v * 10)
    x = (Math.abs(lo) % 2 === 0 ? lo : lo + 1) / 10
  }
  const [whole, frac] = x.toFixed(1).split('.')
  const text = `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${frac}`
  return unit ? `${text} ${unit}` : text
}

/** "2030", "2030 and 2035", "2027, 2030 and 2035" (`evidence._and`). */
function and(items: string[]): string {
  return items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** `evidence._connection_cell`: the limit and the peak import checked
 *  against it. The verdict is the payload's; only the excess's words round. */
export function connectionCell(p: EhSizingConnection): string {
  const check = p.connection_check
  if (check === 'unbounded') {
    return 'unbounded connection: a grid generator sits at the point of '
      + 'connection with no import link, so no limit applies'
  }
  const limit = p.grid_connection_mw
  if (limit == null) return `Not established: ${p.grid_connection_refusal || 'the network sets no connection limit.'}`
  let cell = fmt1(limit, 'MW')
  const peak = p.grid_import_peak_mw
  if (peak == null || (check !== 'within' && check !== 'exceeds')) return cell
  cell += ` · peak import ${fmt1(peak, 'MW')}`
  if (check === 'within') return `${cell} · within the limit`
  // Rounded first, as the report does: 40.05 - 40 is 0.04999..., an excess of 0.05.
  const excess = Math.round((peak - limit) * 1e9) / 1e9
  cell += excess < 0.05 ? ' · exceeds the limit by less than 0.1 MW' : ` · exceeds the limit by ${fmt1(excess, 'MW')}`
  const over = Object.entries(p.grid_import_peak_by_period ?? {})
    .filter(([, v]) => v > limit + CONNECTION_TOL_MW).map(([k]) => k)
  return over.length ? `${cell} · in ${and(over)}` : cell
}

const SLACK_WORDS: Record<string, string> = { shortfall: 'Shortfall', buy_in: 'Bought in' }

/** One row of the block: the report's label and cell, which kind of row it
 *  is, whether the cell is a sentence (text font) rather than figures (mono),
 *  and whether it states an over-limit verdict (warn colour). */
export interface NotCountedRow {
  label: string
  value: string
  kind: 'connection' | 'slack' | 'counted'
  sentence: boolean
  warn: boolean
}

/** The rows in the report's order; null for a payload stored before the
 *  connection was read apart (`grid_connection_mw` absent), which shows
 *  nothing new, as the report renders it (GK A 33 S4). */
export function notCountedRows(payload: unknown): NotCountedRow[] | null {
  if (payload == null || typeof payload !== 'object' || !('grid_connection_mw' in payload)) return null
  const p = payload as EhSizingConnection
  const over = p.horizon ? `over the horizon (${p.horizon})` : 'over the horizon'
  // A refusal or the unbounded words are a sentence; a limit is figures.
  const figures = p.connection_check !== 'unbounded' && p.grid_connection_mw != null
  const rows: NotCountedRow[] = [{ label: GRID_ROW, value: connectionCell(p), kind: 'connection',
    sentence: !figures, warn: figures && p.connection_check === 'exceeds' }]
  for (const s of p.product_slacks ?? []) {
    // A slack that delivered nothing has no row (the report's own rule).
    if (s.energy == null || Math.abs(s.energy) < 1e-9) continue
    rows.push({
      label: `${Object.prototype.hasOwnProperty.call(SLACK_WORDS, s.process_role) ? SLACK_WORDS[s.process_role] : s.process_role}, ${s.carrier || s.name} (not counted)`,
      value: `${fmt1(s.p_nom, s.unit)} · ${fmt1(s.energy, s.energy_unit)} ${over}`,
      kind: 'slack', sentence: false, warn: false,
    })
  }
  for (const line of p.grid_supply_counted ?? []) {
    rows.push({ label: 'Grid supply counted', value: String(line), kind: 'counted', sentence: true, warn: false })
  }
  return rows
}
