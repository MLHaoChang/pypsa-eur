// The value-flow result (GET /results/value_flows, IC P3 WP3.4) shaped for the
// Investment tab (WP3.6): the per-participant table, the Sankey and the
// conservation chip. Pure functions, so every mapping is tested without a DOM.
import type { ValueFlowsPayload } from '../../../api/commercial'

type Period = NonNullable<ValueFlowsPayload['periods']>[string]

/** Money in the tariff's own currency: the payload carries no currency code,
 *  so no symbol is printed (a US tariff is not in euros). */
export function fmtAmount(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** The ledger streams whose money is an annuity, not a cash payment. */
const STREAM_LABEL: Record<string, string> = {
  capex: 'capex (annuity)',
}
export const streamLabel = (s: string) => STREAM_LABEL[s] ?? s.replace(/_/g, ' ')

export function periodKeys(payload: ValueFlowsPayload): string[] {
  return Object.keys(payload.periods ?? {}).sort()
}

export const periodLabel = (k: string) => (k === '_' ? 'Modelled year' : k)

export interface ParticipantRow {
  id: string
  label: string
  internal: boolean
  paid: number | null
  received: number | null
  net: number | null
  streams: Array<{ stream: string; net: number | null }>
}

/** Participants first (in the config's order), then the externals by the
 *  size of their net; a null total stays null (never a partial sum). */
export function participantRows(payload: ValueFlowsPayload, period: Period): ParticipantRow[] {
  const parts = payload.participants ?? []
  const idOf = (x: string) => x.trim().toLowerCase()
  const internal = new Map(parts.map((p, i) => [idOf(p.id), { i, name: p.name }]))
  const rows = Object.entries(period.by_participant ?? {}).map(([id, t]) => {
    const own = internal.get(idOf(id))
    return {
      id, label: own?.name && own.name !== id ? `${own.name} (${id})` : id,
      internal: !!own, paid: t.paid, received: t.received, net: t.net,
      streams: Object.entries(t.by_stream ?? {}).sort(([a], [b]) => a.localeCompare(b))
        .map(([stream, net]) => ({ stream, net })),
      order: own ? own.i : Number.POSITIVE_INFINITY,
    }
  })
  rows.sort((a, b) => a.order - b.order
    || Math.abs(b.net ?? Number.POSITIVE_INFINITY) - Math.abs(a.net ?? Number.POSITIVE_INFINITY)
    || a.id.localeCompare(b.id))
  return rows.map(({ order: _o, ...r }) => r)
}

export type ConservationState =
  | { status: 'ok' }
  | { status: 'failed'; checks: string[] }
  | { status: 'not_established'; checks: string[] }

export function conservationState(c: Period['conservation'] | undefined): ConservationState {
  if (!c) return { status: 'not_established', checks: [] }
  const notTrue = (c.checks ?? []).filter(x => x.ok !== true)
  if (c.ok === true) return { status: 'ok' }
  if (c.ok === false) return { status: 'failed', checks: notTrue.filter(x => x.ok === false).map(x => x.name) }
  return { status: 'not_established', checks: notTrue.map(x => x.name) }
}

export interface SankeyNodeDatum { name: string; id: string; internal: boolean; side: 'payer' | 'payee' }
export interface SankeyLinkDatum { source: number; target: number; value: number; stream: string }

/** The payload's bipartite Sankey (node ids) as recharts needs it (indices).
 *  A link naming a node that is not in the list is dropped, never guessed. */
export function sankeyData(s: Period['sankey'] | undefined): {
  nodes: SankeyNodeDatum[]; links: SankeyLinkDatum[]
} {
  const nodes = (s?.nodes ?? []).map(n => ({ name: n.label, id: n.id, internal: n.internal, side: n.side }))
  const index = new Map(nodes.map((n, i) => [n.id, i]))
  const links = (s?.links ?? [])
    .filter(l => index.has(l.source) && index.has(l.target) && l.value > 0)
    .map(l => ({ source: index.get(l.source)!, target: index.get(l.target)!,
                 value: l.value, stream: l.stream }))
  return { nodes, links }
}

/** CSV rows: one total row per party, then one row per stream (its net). */
export function csvRows(periodKey: string, rows: ParticipantRow[]): unknown[][] {
  const out: unknown[][] = []
  for (const r of rows) {
    out.push([periodKey, r.id, r.internal ? 'participant' : 'external', 'total',
              r.paid, r.received, r.net])
    for (const s of r.streams) out.push([periodKey, r.id, r.internal ? 'participant' : 'external',
                                         s.stream, null, null, s.net])
  }
  return out
}
export const CSV_HEADER = ['period', 'party', 'side', 'stream', 'paid', 'received', 'net']
