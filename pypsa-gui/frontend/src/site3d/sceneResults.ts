// SC-03 (D07): the scene results overlay in words. Pure: no React, no three.
//
// The server projects every number (`services/site_scene/results.py`); this
// module only puts its answer into words, so:
//  - the snapshot asked for is the native label and period the timeline's
//    `/network/snapshots` lists at `resultsSnapshotIdx`: never an hour count,
//    a step length or a year of 8760 hours;
//  - an unavailable number reads as its reason in words, never 0.00, and a
//    genuine zero (`available: true`) reads as 0;
//  - each number keeps its native unit (t/h stays t/h, never MW);
//  - an explicit share is labelled allocated, with the native rate beside it;
//    a target shared between buildings without shares is shown once, unsplit;
//  - a group shows its boundary rows and per-carrier totals as served, and the
//    single total only when the server gives one: nothing is summed here.
import type { SceneResults, SceneResultsQuery, ResultGroup, ResultPort, ResultQuantity, ResultTarget } from '../api/siteSceneResults'
import type { SnapshotInfo } from '../api/types'
import type { SceneBuilding, SceneGroup, TargetClass } from '../api/siteScene'
import { featureName, KIND_WORDS, pct, targetLabel, type UiMode } from './associations'

/** Every reason the route can give (`results.REASONS`, pinned by a parity test), in words after "Unavailable: ". */
export const REASON_WORDS: Readonly<Record<string, string>> = {
  project_not_open: 'the project is not open',
  run_not_solved: 'the model has not been solved yet',
  run_failed: 'the last solve failed',
  run_cancelled: 'the last solve was cancelled',
  run_running: 'a solve is running',
  results_not_current: 'the results are out of date because the model changed after the solve',
  run_changed: 'the results are out of date because a newer solve replaced them',
  snapshot_not_in_run: 'this snapshot is not in the solved run',
  window_out_of_range: 'the time window is outside the solved horizon',
  unbound_target: 'the model item is no longer in the model',
  series_missing: 'the solve wrote no result for this item',
  non_finite_value: 'the solve gave no finite value',
  unit_unknown: 'the bus unit is not one the overlay knows (MW or t)',
  conversion_factor_missing: 'the carrier has no MWh-per-tonne factor',
  vintage_rows_unsupported: 'items with several build years are not shown per hour yet',
  mixed_units: 'there is no single total because the carriers have different units',
  group_partial_allocation: 'a member is shared with buildings outside the group',
  member_unavailable: 'a member’s result is unavailable',
  group_has_no_boundary: 'every bus is inside the group, so nothing crosses its boundary',
}

export const reasonWords = (reason: string | null | undefined): string =>
  (reason && REASON_WORDS[reason]) || 'no value for this snapshot'

/** One sentence, one colon: "Unavailable: <reason>." */
export const unavailableWords = (reason: string | null | undefined): string => `Unavailable: ${reasonWords(reason)}.`

/** A served number in its own unit, at most 4 significant digits; a negative zero reads 0. */
export function num(v: number): string {
  const x = Object.is(v, -0) ? 0 : v
  return x.toLocaleString('en-US', { maximumSignificantDigits: 4 })
}

/**
 * A quantity in words: "13.25 MW", "0 t/h", or "Unavailable: <reason>." — never 0.00 for a missing value.
 * `stated`: a reason already said once above (the run's), which a leaf then reads as plain "Unavailable".
 */
export function quantityWords(q: ResultQuantity | null | undefined, stated: string | null = null): string {
  if (!q || !q.available || q.value == null || !Number.isFinite(q.value)) {
    return stated && q?.reason === stated ? 'Unavailable' : unavailableWords(q?.reason)
  }
  return q.unit ? `${num(q.value)} ${q.unit}` : num(q.value)
}

export const isAvailable = (q: ResultQuantity | null | undefined): boolean =>
  !!q && q.available && q.value != null && Number.isFinite(q.value)

/** The same quantity without its sign (Guided puts the direction in a word instead). */
const magnitude = (q: ResultQuantity): ResultQuantity => (isAvailable(q) ? { ...q, value: Math.abs(q.value!) } : q)

// ── the timeline's snapshot as native labels ───────────────────────────────

/**
 * The snapshot at the timeline's index, as `/network/snapshots` lists it: its
 * label and, on a multi-period model, its period. Null when the index is not
 * a snapshot of the model (nothing is clamped or guessed).
 */
export function snapshotQueryAt(info: Pick<SnapshotInfo, 'snapshots' | 'periods'> | null | undefined, idx: number): SceneResultsQuery | null {
  const labels = info?.snapshots
  if (!labels || !Number.isInteger(idx) || idx < 0 || idx >= labels.length) return null
  const p = info?.periods?.[idx]
  return { snapshot: String(labels[idx]), period: p == null ? null : String(p) }
}

/** The served snapshot in words: "2030-01-01 03:00 · period 2030 · weight 1 h". */
export function snapshotWords(r: SceneResults): string {
  const s = r.snapshot
  if (!s) return 'No snapshot'
  const parts = [typeof s.label === 'string' ? s.label.replace('T', ' ') : `Snapshot ${s.label}`]
  if (s.period != null) parts.push(`period ${s.period}`)
  if (isAvailable(s.weight_generators)) parts.push(`weight ${quantityWords(s.weight_generators)}`)
  return parts.join(' · ')
}

// ── targets on a feature ───────────────────────────────────────────────────

export interface PortLine {
  key: string
  label: string
  /** The headline value: the allocated part for an explicit share, else the native rate. */
  value: string
  available: boolean
  /** "allocated" for an explicit share, else null. */
  tag: string | null
  /** Secondary words: the native rate beside an allocated value, a tonne equivalent, "reversed". */
  notes: string[]
}

export interface TargetView {
  key: string
  title: string
  /** "allocated 40 %", "shared with Building 2 · not split", or null for a whole target. */
  allocation: string | null
  /** A target-level reason (unbound, several build years) in words, else null. */
  reason: string | null
  level: string | null
  ports: PortLine[]
}

export interface Names { buildings: readonly SceneBuilding[]; groups: readonly SceneGroup[] }

const ROLE_WORDS: Record<string, string> = { input: 'in', output: 'out', storage: 'storage', unknown: 'role unknown' }

const targetKey = (t: ResultTarget): string => t.uid ?? `${t.class}:${t.name}`

function titleOf(t: ResultTarget, mode: UiMode): string {
  return t.class in KIND_WORDS ? targetLabel(t.class as TargetClass, t.name, mode) : `${t.name} · ${t.class}`
}

/** The run's reason when its gate is closed: said once in the heading, so the leaves do not repeat it. */
export const runReason = (r: SceneResults): string | null => (r.run.current ? null : r.run.reason)

/**
 * Guided: the flow's actual direction as a word ("in" into the item, "out" of it), from the served value's
 * sign and the port's sign convention, so the number is shown without a minus. Zero or unavailable: the role.
 */
function direction(p: ResultPort, q: ResultQuantity): string {
  if (!isAvailable(q) || q.value === 0) return ROLE_WORDS[p.role] ?? p.role
  return (p.sign === 'withdrawal_positive') === (q.value! > 0) ? 'in' : 'out'
}

function portLine(p: ResultPort, explicit: boolean, mode: UiMode, stated: string | null): PortLine {
  const guided = mode === 'guided'
  const main = explicit && p.allocated ? p.allocated : p.rate
  const shown = (q: ResultQuantity) => quantityWords(guided ? magnitude(q) : q, stated)
  const label = guided ? `${p.carrier || p.bus} ${direction(p, main)}` : `${p.port} · ${p.bus} · ${ROLE_WORDS[p.role] ?? p.role}`
  const notes: string[] = []
  if (explicit && p.allocated && isAvailable(p.rate)) notes.push(`of ${shown(p.rate)} in total`)
  if (p.rate_t && isAvailable(p.rate_t)) notes.push(shown(p.rate_t))
  if (p.reversed) notes.push('reversed')
  return { key: p.port, label, value: shown(main), available: isAvailable(main), tag: explicit && p.allocated ? 'allocated' : null, notes }
}

function allocationWords(t: ResultTarget, here: string | null, names: Names): string | null {
  const a = t.allocation
  if (!a) return null
  if (a.basis === 'explicit') return `allocated ${pct(a.weight * 100)}`
  const others = a.features.filter(f => f !== here).map(f => featureName(f, names.buildings, names.groups))
  return others.length ? `shared with ${others.join(', ')} · not split` : 'shared · not split'
}

export function targetView(t: ResultTarget, here: string | null, names: Names, mode: UiMode, stated: string | null = null): TargetView {
  const explicit = t.allocation?.basis === 'explicit'
  return {
    key: targetKey(t),
    title: titleOf(t, mode),
    allocation: allocationWords(t, here, names),
    reason: t.reason ? unavailableWords(t.reason) : null,
    level: t.level ? quantityWords(t.level, stated) : null,
    ports: t.ports.map(p => portLine(p, explicit, mode, stated)),
  }
}

/** One feature's targets (a building's attachments), in words; empty when nothing is attached. */
export function featureView(r: SceneResults, feature: string, names: Names, mode: UiMode): TargetView[] {
  return (r.features[feature]?.targets ?? []).map(t => targetView(t, feature, names, mode, runReason(r)))
}

export interface SummaryRow { view: TargetView; on: string[] }

/**
 * Every target on the scene once, with the features it sits on. A target
 * shared between buildings is one row (its native rate, never repeated per
 * building); an explicit share keeps its allocated rows per building.
 */
export function sceneSummary(r: SceneResults, names: Names, mode: UiMode): SummaryRow[] {
  const rows = new Map<string, SummaryRow>()
  for (const [feature, f] of Object.entries(r.features)) {
    for (const t of f.targets) {
      const shared = t.allocation?.basis === 'shared'
      const key = shared ? `shared|${targetKey(t)}` : `${feature}|${targetKey(t)}`
      const where = featureName(feature, names.buildings, names.groups)
      const row = rows.get(key)
      if (row) { if (!row.on.includes(where)) row.on.push(where); continue }
      rows.set(key, { view: targetView(t, shared ? null : feature, names, mode, runReason(r)), on: [where] })
    }
  }
  return [...rows.values()]
}

// ── groups ──────────────────────────────────────────────────────────────────

export interface GroupLine { key: string; label: string; value: string; available: boolean }
export interface GroupView {
  boundary: GroupLine[]
  totals: GroupLine[]
  /** The single total as served ("Unavailable: no single total: …" when the units differ). */
  total: string
  notes: string[]
}

/**
 * A group's boundary rows and per-carrier totals, as served; positive is a net import into the group.
 * Expert shows the signed net import; Guided says import or export and shows the number without a sign.
 */
export function groupView(g: ResultGroup, mode: UiMode, stated: string | null = null): GroupView {
  const guided = mode === 'guided'
  const flow = (where: string, q: ResultQuantity) => {
    if (!guided) return { label: where, value: quantityWords(q, stated) }
    const word = !isAvailable(q) || q.value === 0 ? 'net import' : q.value! > 0 ? 'import' : 'export'
    return { label: `${where} ${word}`, value: quantityWords(magnitude(q), stated) }
  }
  const boundary = g.boundary.map(b => ({
    key: `${b.bus}|${b.carrier ?? ''}`,
    ...flow(guided ? (b.carrier || b.bus) : `${b.bus} · ${b.carrier ?? 'no carrier'}`, b.net_import),
    available: isAvailable(b.net_import),
  }))
  const totals = g.totals.map((t, i) => ({
    key: `${t.carrier ?? ''}|${t.net_import.unit ?? ''}|${i}`,
    ...flow(t.carrier || 'no carrier', t.net_import),
    available: isAvailable(t.net_import),
  }))
  const notes: string[] = []
  if (g.internal_buses.length) notes.push(`Inside the group and not counted: ${g.internal_buses.join(', ')}`)
  if (g.unavailable_member) notes.push(unavailableWords('member_unavailable'))
  return { boundary, totals, total: quantityWords(g.total, stated), notes }
}

/** The groups a feature sits in, directly or through a nested group (outermost last). */
export function groupsOf(feature: string, groups: readonly SceneGroup[]): SceneGroup[] {
  const out: SceneGroup[] = []
  const seen = new Set<string>()
  let frontier = [feature]
  while (frontier.length) {
    const next: string[] = []
    for (const g of groups) {
      if (seen.has(g.id) || !g.members.some(m => frontier.includes(m))) continue
      seen.add(g.id); out.push(g); next.push(g.id)
    }
    frontier = next
  }
  return out
}
