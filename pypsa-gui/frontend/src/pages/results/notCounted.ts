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
}

/** `eh_report.CONNECTION_TOL_MW`: the peak may sit this far above the limit
 *  and still read "within" (pinned on both sides by the parity fixture). */
export const CONNECTION_TOL_MW = 1e-6

export const GRID_ROW = 'Grid connection (import limit, not counted)'

/** `formatting.fmt_number(v, unit, digits=1)`: Python's `f"{v:,.1f}"`,
 *  which rounds an exact tie (32.25, a quarter) to even ("32.2") where
 *  `toLocaleString` rounds it up; a tie at one digit is always a quarter, so
 *  it is found exactly. */
export function fmt1(v: number | null | undefined, unit?: string | null): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  let x = v
  if (Number.isInteger(v * 4) && !Number.isInteger(v * 2)) {
    const lo = Math.floor(v * 10)
    x = (Math.abs(lo) % 2 === 0 ? lo : lo + 1) / 10
  }
  const text = x.toLocaleString('en-GB', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  return unit ? `${text} ${unit}` : text
}

function connectionCell(p: EhSizingConnection): string {
  const check = p.connection_check
  if (check === 'unbounded') {
    return 'unbounded connection: a grid generator sits at the point of '
      + 'connection with no import link, so no limit applies'
  }
  const limit = p.grid_connection_mw
  if (limit == null) return `not established: ${p.grid_connection_refusal || 'no connection limit'}`
  let cell = fmt1(limit, 'MW')
  const peak = p.grid_import_peak_mw
  if (peak != null && (check === 'within' || check === 'exceeds')) {
    cell += `; peak import ${fmt1(peak, 'MW')}, ${check === 'within' ? 'within' : 'EXCEEDS'} the limit`
    const over = Object.entries(p.grid_import_peak_by_period ?? {})
      .filter(([, v]) => v > limit + CONNECTION_TOL_MW).map(([k]) => k)
    if (over.length) cell += ` in ${over.join(', ')}`
  }
  return cell
}

const SLACK_WORDS: Record<string, string> = { shortfall: 'Shortfall', buy_in: 'Bought in' }

/** The rows, label then cell; null for a payload stored before the
 *  connection was read apart (`grid_connection_mw` absent), which shows
 *  nothing new, as the report renders it (GK A 33 S4). */
export function notCountedRows(payload: unknown): [string, string][] | null {
  if (payload == null || typeof payload !== 'object' || !('grid_connection_mw' in payload)) return null
  const p = payload as EhSizingConnection
  const rows: [string, string][] = [[GRID_ROW, connectionCell(p)]]
  for (const s of p.product_slacks ?? []) {
    // A slack that delivered nothing has no row (the report's own rule).
    if (s.energy == null || Math.abs(s.energy) < 1e-9) continue
    rows.push([
      `${SLACK_WORDS[s.process_role] ?? s.process_role}, ${s.carrier || s.name} (not counted)`,
      `${fmt1(s.p_nom, s.unit)}; ${fmt1(s.energy, s.energy_unit)} over the horizon`,
    ])
  }
  for (const line of p.grid_supply_counted ?? []) rows.push(['Grid supply counted', String(line)])
  return rows
}
