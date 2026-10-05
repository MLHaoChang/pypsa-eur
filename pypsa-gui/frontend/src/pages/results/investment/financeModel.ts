// The finance inputs form (IC P4 WP4.7a) and the investment-case report
// (WP4.7b) as pure functions, so every mapping is tested without a DOM.
//
// The form edits the loaded `FinanceInputs` in place of a copy: a key the user
// never touches keeps its stored shape (absent stays absent), so a save
// without edits PUTs exactly what was read. An empty input is `null` / absent —
// "not stated" (plan C12) — never 0. The server judges every combination; its
// 422 is mapped to the field it names (the WP3.7 pattern).
import type {
  CashflowLine, FinanceInputs, InvestmentCaseReportPayload, InvestmentCaseStudy, IcSectionState,
} from '../../../api/types'
import type { CompletenessRow } from '../../../components/CompletenessChips'
import { fmtAmount } from './valueFlows'

/** The form's draft: a stored object, or a new one the server will judge. */
export type FinanceDraft = Partial<FinanceInputs> & Record<string, unknown>

export const NOT_STATED = 'not stated'
export const NOT_ESTABLISHED = 'not established'

// ── edits ────────────────────────────────────────────────────────────────

/** A copy of `obj` with `key` set; `undefined` removes the key. */
export function withKey<T extends object>(obj: T, key: string, value: unknown): T {
  const out = { ...obj } as Record<string, unknown>
  if (value === undefined) delete out[key]
  else out[key] = value
  return out as T
}

/** A number input's text: '' → null (not stated), never 0. What was typed is
 *  sent — 2.5 years is the server's to refuse, never truncated. */
export const numOrNull = (text: string): number | null => (text.trim() === '' ? null : Number(text))

export const numText = (v: unknown): string => (typeof v === 'number' ? String(v) : '')

/** "0.05" → 0.05; "0.05, 0.055" (any comma) → a list; '' → undefined (not
 *  stated); anything that is not numbers → null (invalid). */
export function parseRateOrList(text: string): number | number[] | undefined | null {
  const t = text.trim()
  if (!t) return undefined
  if (t.includes(',')) {
    const parts = t.split(',').map(x => x.trim()).filter(Boolean).map(Number)
    return parts.length && parts.every(Number.isFinite) ? parts : null
  }
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** A one-element list keeps a trailing comma so it re-parses as a list. */
export function rateOrListText(v: unknown): string {
  if (Array.isArray(v)) return v.length === 1 ? `${v[0]},` : v.join(', ')
  return typeof v === 'number' ? String(v) : ''
}

/** "1, 0" → [1, 0]; '' → []; not numbers → null. */
export function parseNumberList(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean).map(Number)
  return parts.every(Number.isFinite) ? parts : null
}

/** A tri-state (bool | null) select value. */
export const triText = (v: unknown) => (v === true ? 'yes' : v === false ? 'no' : '')
export const triValue = (t: string): boolean | null => (t === 'yes' ? true : t === 'no' ? false : null)

// ── the server's 422, by field ───────────────────────────────────────────

/** `['body', 'finance', 'debt', 0, 'rate']` → `debt.0.rate`; a model-level
 *  error (`['body', 'finance']`) → '' (the form's). */
export function errorKey(loc: Array<string | number>): string {
  const rest = [...loc]
  if (rest[0] === 'body') rest.shift()
  if (rest[0] === 'finance') rest.shift()
  return rest.join('.')
}

/** A 422 `detail` by field key: pydantic's list of `{loc, msg}` (FastAPI's
 *  body validation), the route's `{code, message, errors: [{loc, msg}]}`, or
 *  a `{code, message}` / string refusal (the form's own, key ''). */
export function financeErrors(detail: unknown): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  const push = (k: string, m: string) => { (out[k] ??= []).push(m) }
  const list = (items: unknown[]) => {
    for (const e of items as Array<{ loc?: Array<string | number>; msg?: string } | string>) {
      if (typeof e === 'string') push('', e)
      else push(errorKey(e?.loc ?? []), String(e?.msg ?? 'invalid'))
    }
  }
  if (Array.isArray(detail)) {
    list(detail)
  } else if (typeof detail === 'string') {
    push('', detail)
  } else if (detail && typeof detail === 'object') {
    const d = detail as { code?: string; message?: string; loc?: Array<string | number>; field?: string
                          errors?: unknown }
    if (Array.isArray(d.errors) && d.errors.length) list(d.errors)
    else push(Array.isArray(d.loc) ? errorKey(d.loc) : (d.field ?? ''), d.message ?? d.code ?? 'refused')
  }
  return out
}

const under = (key: string, path: string) =>
  path === '' || key === path || key.startsWith(`${path}.`)

/** The errors at or under `path` that no path in `placed` claims (a field's
 *  own, or a section's leftovers once its fields took theirs). */
export function errorsFor(errors: Record<string, string[]>, path: string,
                          placed: string[] = []): string[] {
  return Object.entries(errors)
    .filter(([k]) => under(k, path) && !placed.some(p => under(k, p)))
    .flatMap(([k, msgs]) => msgs.map(m => (k === path || !k ? m : `${k.slice(path ? path.length + 1 : 0)}: ${m}`)))
}

// ── the report ───────────────────────────────────────────────────────────

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v)

/** A value as a number, or null when it is not established (null, absent,
 *  the string "not_established", a `{value: null}` record). */
export function established(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (isObj(v) && 'value' in v) return established(v.value)
  return null
}

export function sectionOf(report: InvestmentCaseReportPayload | null | undefined,
                          name: string): IcSectionState | null {
  const s = report?.sections?.[name]
  return isObj(s) ? s as IcSectionState : null
}

function payloadOf(report: InvestmentCaseReportPayload | null | undefined, name: string): Obj {
  const p = sectionOf(report, name)?.payload
  if (!isObj(p)) return {}
  // A payload may nest its metrics; both levels are read.
  return isObj(p.metrics) ? { ...p.metrics, ...p } : p
}

export type HeadlineKind = 'pct' | 'money' | 'ratio' | 'years' | 'price'

export interface Headline {
  id: string
  label: string
  kind: HeadlineKind
  value: number | null
  /** Why it is not established, when the report says. */
  note: string | null
}

interface HeadlineSpec {
  id: string; label: string; kind: HeadlineKind
  top?: string; section?: string; keys?: string[]
  /** Shown only when the report carries the key at all. */
  optional?: boolean
}

const HEADLINES: HeadlineSpec[] = [
  { id: 'project_irr_pre_tax', label: 'Unlevered project IRR, pre-tax', kind: 'pct',
    top: 'project_irr_pre_tax', section: 'project', keys: ['project_pre_tax_irr'] },
  { id: 'project_irr_post_tax', label: 'Unlevered project IRR, post-tax', kind: 'pct',
    top: 'project_irr_post_tax', section: 'project', keys: ['project_post_tax_irr'] },
  { id: 'npv_at_wacc', label: 'Unlevered NPV at WACC', kind: 'money',
    top: 'npv_at_wacc', section: 'project', keys: ['project_post_tax_npv'] },
  { id: 'equity_irr_pre_tax', label: 'Equity IRR, pre-tax', kind: 'pct',
    section: 'project', keys: ['equity_pre_tax_irr', 'equity_irr_pre_tax'] },
  { id: 'equity_irr_post_tax', label: 'Equity IRR, post-tax', kind: 'pct',
    section: 'project', keys: ['equity_post_tax_irr', 'equity_irr_post_tax'] },
  { id: 'equity_npv_post_tax', label: 'Equity NPV, post-tax (at cost of equity)', kind: 'money',
    section: 'project', keys: ['equity_post_tax_npv', 'equity_npv_post_tax'] },
  { id: 'lifecycle_npv', label: 'Lifecycle-cost NPV (total, not incremental)', kind: 'money',
    section: 'project', keys: ['lifecycle_npv', 'lifecycle_cost_npv'] },
  { id: 'payback_years', label: 'Payback (post-tax equity)', kind: 'years',
    section: 'project', keys: ['payback_years', 'payback'] },
  { id: 'min_dscr', label: 'Minimum DSCR', kind: 'ratio', top: 'min_dscr', section: 'debt', keys: ['min_dscr'] },
  { id: 'avg_dscr', label: 'Average DSCR', kind: 'ratio', top: 'avg_dscr', section: 'debt', keys: ['avg_dscr'] },
  { id: 'llcr', label: 'LLCR', kind: 'ratio', top: 'llcr', section: 'debt', keys: ['llcr'] },
  { id: 'plcr', label: 'PLCR', kind: 'ratio', top: 'plcr', section: 'debt', keys: ['plcr'] },
  { id: 'lcoe', label: 'LCOE (finance-consistent, nominal)', kind: 'price',
    top: 'lcoe_finance_consistent_eur_per_mwh', section: 'project', keys: ['lcoe_nominal_per_mwh'] },
  { id: 'lcoe_real', label: 'LCOE (real)', kind: 'price', section: 'project',
    keys: ['lcoe_real_per_mwh'], optional: true },
  { id: 'ppa_price', label: 'Solved PPA price for the target IRR', kind: 'price',
    top: 'ppa_price_for_target_irr_eur_per_mwh', section: 'project', keys: ['solved_ppa_price'] },
]

export function headlines(report: InvestmentCaseReportPayload): Headline[] {
  const top = report as Obj
  const out: Headline[] = []
  for (const h of HEADLINES) {
    const payload = h.section ? payloadOf(report, h.section) : {}
    const hasTop = !!h.top && h.top in top
    const key = (h.keys ?? []).find(k => k in payload)
    if (h.optional && !hasTop && !key) continue
    // The report's own headline wins, a null one included (it is not
    // established — a section number never overrides it); the section payload
    // fills only a headline the export view does not carry.
    const raw = hasTop ? top[h.top!] : key ? payload[key] : null
    const value = established(raw)
    let note: string | null = null
    if (value === null) {
      const sec = h.section ? sectionOf(report, h.section) : null
      note = (isObj(raw) && typeof raw.reason === 'string' ? raw.reason : null)
        ?? (h.id === 'ppa_price' && typeof payload.solve_ppa_status === 'string' ? payload.solve_ppa_status : null)
        ?? (sec && sec.status !== 'ok' ? sec.note ?? `the ${h.section} section is ${sec.status.replace(/_/g, ' ')}` : null)
    }
    out.push({ id: h.id, label: h.label, kind: h.kind, value, note })
  }
  return out
}

/** The keys the headline row already shows, so a section's detail omits them. */
export const HEADLINE_KEYS = new Set(HEADLINES.flatMap(h => h.keys ?? []))

export function fmtHeadline(kind: HeadlineKind, v: number | null): string {
  if (v === null) return NOT_ESTABLISHED
  switch (kind) {
    case 'pct': return `${(v * 100).toFixed(2)} %`
    case 'ratio': return `${v.toFixed(2)}×`
    case 'years': return `${v.toFixed(1)} years`
    case 'price': return `${fmtAmount(v)} per MWh`
    default: return fmtAmount(v)
  }
}

export type GateState = 'consistent' | 'differs' | 'not_established'

/** The WACC gate (plan C10): `true` consistent, `false` differs, `null` /
 *  absent not established — never read as consistent. */
export function waccGate(report: InvestmentCaseReportPayload): GateState {
  const v = report.gates && 'wacc_vs_discount_rate_consistent' in report.gates
    ? report.gates.wacc_vs_discount_rate_consistent : report.wacc_vs_discount_rate_consistent
  return v === true ? 'consistent' : v === false ? 'differs' : 'not_established'
}

/** The gate's legs (`discount_rate`, `asset_rates`, `inflation`) where the
 *  gates section carries them. */
export function gateLegs(report: InvestmentCaseReportPayload): Array<[string, string]> {
  const p = payloadOf(report, 'gates')
  const block = isObj(p.wacc) ? p.wacc : p
  const legs = isObj(block.legs) ? block.legs : null
  if (!legs) return []
  return Object.entries(legs).map(([k, v]) => [k.replace(/_/g, ' '),
    v == null ? NOT_ESTABLISHED : String(v)])
}

/** The incremental-vs-counterfactual statement (plan C13) as the report
 *  states it, or null when it states none. */
export function counterfactualStatement(report: InvestmentCaseReportPayload): string | null {
  const p = payloadOf(report, 'project')
  for (const k of ['counterfactual_statement', 'incremental_statement', 'statement']) {
    if (typeof p[k] === 'string' && p[k]) return p[k] as string
  }
  const cf = p.counterfactual
  if (typeof cf === 'string' && cf) return cf
  if (isObj(cf)) {
    for (const k of ['statement', 'description', 'basis']) {
      if (typeof cf[k] === 'string' && cf[k]) return cf[k] as string
    }
  }
  if (p.has_counterfactual === true) {
    return 'This case has a counterfactual: the returns are on the owner\'s cash minus the site\'s supply '
      + 'cost without the owner\'s assets (the counterfactual net is in the project detail).'
  }
  if (p.has_counterfactual === false) {
    return 'This case has no counterfactual: the owner is not the site party that pays the bill, so the '
      + 'returns are on the owner\'s own cash.'
  }
  return null
}

/** Every string flag the report's sections carry, deduplicated. */
export function reportFlags(report: InvestmentCaseReportPayload, section: string): string[] {
  const p = payloadOf(report, section)
  const flags = [p.flags, isObj(p.counterfactual) ? p.counterfactual.flags : undefined]
    .flatMap(f => (Array.isArray(f) ? f : [])).filter((x): x is string => typeof x === 'string')
  return [...new Set(flags)]
}

/** Plain chip labels for the report sections (UX assessment Q7). */
const SECTION_LABELS: Record<string, string> = {
  design: 'Design', commercial: 'Commercial', dispatch_modes: 'Dispatch modes',
  participants: 'Participants', project: 'Project', debt: 'Debt', tax: 'Tax',
  tax_equity: 'Tax equity', uncertainty: 'Uncertainty', gates: 'Gates',
}

export function completenessRows(report: InvestmentCaseReportPayload): CompletenessRow[] {
  return Object.entries(report.completeness ?? {}).map(([name, status]) => ({
    name, status: String(status ?? 'not_established'),
    label: SECTION_LABELS[name] ?? name.replace(/_/g, ' '),
    note: sectionOf(report, name)?.note ?? null,
  }))
}

// ── the cashflow table (year × stream) ───────────────────────────────────

export interface CashflowPivot {
  years: number[]
  columns: string[]
  /** year → column → summed amount; a missing cell has no line. */
  cells: Record<number, Record<string, number>>
  totals: Record<number, number>
  /** year → the columns holding a line whose amount is NOT established (the
   *  report lists them apart: a cashflow line's amount is a number). Such a
   *  cell reads "not established", never "–" or a partial sum (P4 gate B2). */
  unknown: Record<number, string[]>
}

interface MissingLine { value_stream?: string | null; source?: string | null;
                        years?: number[] | null }

/** A counterfactual line (source `counterfactual:*`): the supply cost the
 *  site would pay without the investment, NEGATED — returns are on the
 *  incremental cash (C13). It is an avoided cost, not money received. */
export const isCounterfactualLine = (l: CashflowLine) =>
  typeof l.provenance?.source === 'string' && l.provenance.source.startsWith('counterfactual:')

export const AVOIDED_PREFIX = 'avoided vs counterfactual'

export function cashflowPivot(lines: CashflowLine[] | null | undefined,
                              missing?: MissingLine[] | null, owner?: string | null): CashflowPivot {
  const list = (lines ?? []).filter(l => l && typeof l.year === 'number')
  const parties = new Set(list.map(l => l.participant))
  // The counterfactual's lines get their own columns (WP4.7 review round 2,
  // R2-2): merged into the actual stream they would read as money received.
  const stream = (l: CashflowLine) =>
    (isCounterfactualLine(l) ? `${AVOIDED_PREFIX} · ${l.value_stream}` : l.value_stream)
  const col = (l: CashflowLine) => (parties.size > 1 ? `${l.participant} · ${stream(l)}` : stream(l))
  const columns: string[] = []
  const cells: Record<number, Record<string, number>> = {}
  const totals: Record<number, number> = {}
  for (const l of list) {
    const c = col(l)
    if (!columns.includes(c)) columns.push(c)
    const row = (cells[l.year] ??= {})
    row[c] = (row[c] ?? 0) + l.amount
    totals[l.year] = (totals[l.year] ?? 0) + l.amount
  }
  const unknown: Record<number, string[]> = {}
  for (const m of missing ?? []) {
    if (!m || typeof m.value_stream !== 'string') continue
    const stream = typeof m.source === 'string' && m.source.startsWith('counterfactual:')
      ? `${AVOIDED_PREFIX} · ${m.value_stream}` : m.value_stream
    const c = parties.size > 1 ? `${owner ?? 'owner'} · ${stream}` : stream
    if (!columns.includes(c)) columns.push(c)
    for (const y of m.years ?? []) {
      if (typeof y !== 'number') continue
      cells[y] ??= {}
      const u = (unknown[y] ??= [])
      if (!u.includes(c)) u.push(c)
    }
  }
  const years = Object.keys(cells).map(Number).sort((a, b) => a - b)
  return { years, columns, cells, totals, unknown }
}

/** The project payload's not-established lines (P4 gate B2). */
export function missingLines(report: InvestmentCaseReportPayload | null | undefined): MissingLine[] {
  const m = payloadOf(report, 'project').lines_not_established
  return Array.isArray(m) ? m.filter(isObj) as MissingLine[] : []
}

/** The per-year Total of the cashflow table (WP4.7 review round 2, R2-1): the
 *  engine's post-tax equity cash — the backend guarantees each year's lines
 *  sum to it — or null where it is not established, with the reason. A year
 *  whose lines do not sum to it is listed in `mismatch` (never hidden). */
export function cashTotals(report: InvestmentCaseReportPayload | null | undefined,
                           pivot: CashflowPivot):
    { totals: Record<number, number | null>; reason: string | null; mismatch: number[] } {
  const project = sectionOf(report, 'project')
  const years = reportYears(report)
  const cash = payloadOf(report, 'project').cash
  const eq = isObj(cash) ? (cash as Obj).equity_post_tax : undefined
  const totals: Record<number, number | null> = {}
  const mismatch: number[] = []
  const series = Array.isArray(eq) && years && eq.length === years.length ? eq as unknown[] : null
  for (const y of pivot.years) {
    const i = years ? years.indexOf(y) : -1
    const v = series && i >= 0 ? series[i] : null
    totals[y] = typeof v === 'number' && Number.isFinite(v) ? v : null
    const t = totals[y]
    if (t !== null && Math.abs((pivot.totals[y] ?? 0) - t) > 0.01 + 1e-9 * Math.abs(t)) mismatch.push(y)
  }
  let reason: string | null = null
  if (Object.values(totals).some(v => v === null)) {
    const status = (report?.completeness as Record<string, string> | undefined)?.project ?? project?.status
    const tax = sectionOf(report, 'tax')
    const taxStatus = (report?.completeness as Record<string, string> | undefined)?.tax ?? tax?.status
    if (status !== undefined && status !== 'ok') reason = project?.note ?? `the project section is ${status}`
    else if (taxStatus !== undefined && taxStatus !== 'ok') reason = `the tax section: ${tax?.note ?? taxStatus}`
    else reason = 'the post-tax equity cash is not established'
  }
  return { totals, reason, mismatch }
}

// ── generic payload tables ───────────────────────────────────────────────

export interface Table { columns: string[]; rows: Array<Record<string, unknown>> }

const isPrim = (v: unknown) => v === null || ['number', 'string', 'boolean'].includes(typeof v)
const YEARISH = ['year', 'years', 'operating_year', 'axis_year']

function orderColumns(cols: string[]): string[] {
  const first = cols.filter(c => YEARISH.includes(c))
  return [...first, ...cols.filter(c => !YEARISH.includes(c))]
}

/** The report's year axis (the project payload's `years`), for per-year
 *  lists that carry none of their own. */
export function reportYears(report: InvestmentCaseReportPayload | null | undefined): number[] | null {
  const y = sectionOf(report, 'project')?.payload?.years
  return Array.isArray(y) && y.every(v => typeof v === 'number') ? y as number[] : null
}

const isNumList = (v: unknown): v is Array<number | null> =>
  Array.isArray(v) && v.length > 0 && v.every(x => x === null || typeof x === 'number')

/** The per-year lists of a record — the number lists of the axis length (or,
 *  without an axis, of the most common length) — as one columnar record. */
export function perYearLists(value: Record<string, unknown>, axis?: number[] | null): Record<string, unknown> {
  const lists = Object.entries(value).filter(([, v]) => isNumList(v)) as Array<[string, unknown[]]>
  if (!lists.length) return {}
  let n = axis?.length
  if (!n || !lists.some(([, v]) => v.length === n)) {
    const counts = new Map<number, number>()
    for (const [, v] of lists) counts.set(v.length, (counts.get(v.length) ?? 0) + 1)
    n = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0]
  }
  return Object.fromEntries(lists.filter(([, v]) => v.length === n))
}

/** A value as a table: a list of flat records (a row per record), or a record
 *  of equal-length lists (a column per list — the columnar per-year shape;
 *  `axis` labels its rows by year when it carries no year list of its own).
 *  Anything else → null (rendered as a nested block). */
export function toTable(value: unknown, axis?: number[] | null): Table | null {
  if (Array.isArray(value)) {
    if (!value.length || !value.every(r => isObj(r) && Object.values(r).every(isPrim))) return null
    const cols: string[] = []
    for (const r of value as Obj[]) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k)
    return { columns: orderColumns(cols), rows: value as Obj[] }
  }
  if (isObj(value)) {
    const arrays = Object.entries(value).filter(([, v]) => Array.isArray(v) && v.length > 0 && v.every(isPrim))
    if (!arrays.length) return null
    const n = (arrays[0][1] as unknown[]).length
    if (!arrays.every(([, v]) => (v as unknown[]).length === n)) return null
    const cols = orderColumns(arrays.map(([k]) => (k === 'years' ? 'year' : k)))
    const rows = Array.from({ length: n }, (_, i) => Object.fromEntries(
      arrays.map(([k, v]) => [k === 'years' ? 'year' : k, (v as unknown[])[i]])))
    if (!cols.some(c => YEARISH.includes(c))) {
      if (axis && axis.length === n) {
        return { columns: ['year', ...cols], rows: rows.map((r, i) => ({ year: axis[i], ...r })) }
      }
      return { columns: ['#', ...cols], rows: rows.map((r, i) => ({ '#': i + 1, ...r })) }
    }
    return { columns: cols, rows }
  }
  return null
}

/** A table cell: null → "not established" (never 0 or blank). */
export function fmtCell(column: string, v: unknown): string {
  if (v === null || v === undefined || v === 'not_established') return NOT_ESTABLISHED
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v !== 'number') return String(v)
  if (!Number.isFinite(v)) return NOT_ESTABLISHED
  const c = column.toLowerCase()
  if (YEARISH.includes(c) || c === '#') return String(v)
  if (/dscr|llcr|plcr|cover/.test(c)) return v.toFixed(2)
  if (/rate|share|irr|pct|factor|gearing/.test(c)) return String(Number(v.toPrecision(6)))
  return fmtAmount(v)
}

export const label = (k: string) => k.replace(/_/g, ' ')

// ── the study's progress ─────────────────────────────────────────────────

/** A fraction in [0, 1] when the study reports one, else null. */
export function progressFraction(p: unknown): number | null {
  if (typeof p === 'number' && Number.isFinite(p)) return p > 1 ? Math.min(p / 100, 1) : Math.max(p, 0)
  if (isObj(p)) {
    for (const k of ['fraction', 'progress']) {
      if (typeof p[k] === 'number') return progressFraction(p[k])
    }
    const done = p.done ?? p.current ?? p.completed
    const total = p.total
    if (typeof done === 'number' && typeof total === 'number' && total > 0) return Math.min(done / total, 1)
  }
  return null
}

/** The progress as the user reads it ("45 %", "tax (3 of 5)"), or null. */
export function progressText(p: unknown): string | null {
  const f = progressFraction(p)
  const stage = isObj(p) ? (p.stage ?? p.step ?? p.label ?? p.message) : null
  const bits = [typeof stage === 'string' && stage ? label(stage) : null,
    isObj(p) && typeof (p.done ?? p.current) === 'number' && typeof p.total === 'number'
      ? `${p.done ?? p.current} of ${p.total}` : null,
    f !== null ? `${Math.round(f * 100)} %` : null].filter(Boolean)
  return bits.length ? bits.join(' · ') : null
}

// ── the study record ─────────────────────────────────────────────────────

/** The progress the study reports: its `progress`, or the runner's stages. */
export function studyProgress(s: InvestmentCaseStudy | null | undefined): unknown {
  if (!s) return null
  if (s.progress != null) return s.progress
  if (s.stage || s.stages_done) {
    return { stage: s.stage ?? undefined, done: s.stages_done?.length,
             total: s.stages?.length || undefined }
  }
  return null
}

/** Stale: the record's own flag or the stored report's staleness block. */
export function studyStale(s: InvestmentCaseStudy | null | undefined):
    { stale: boolean; changed: string[]; reason: string | null } {
  const stale = s?.stale === true || s?.report?.stale === true
  const rep = s?.report as { reason?: unknown } | undefined
  const reason = stale && typeof rep?.reason === 'string' ? rep.reason : null
  return { stale, changed: stale ? (s?.report?.changed ?? []).filter(x => typeof x === 'string') : [],
           reason }
}

/** Why the currency of a report cannot be checked right now (WP4.7 review B4):
 *  the backend marks it stale with no changed input when it cannot compute
 *  the current key. Null when the reason names changed inputs. */
export function uncheckableReason(reason: string | null): string | null {
  switch (reason) {
    case 'solve_in_flight': return "can't check whether this report is current right now (a solve is running)"
    case 'network_busy': return "can't check whether this report is current right now (the network is busy)"
    case 'current_assumptions_not_established':
      return "can't check whether this report is current: the current inputs are not established"
    default: return null
  }
}

/** The failure a finished run ended in (error / failed / refused), or null. */
export function studyFailure(s: InvestmentCaseStudy | null | undefined): string | null {
  if (!s || !['error', 'failed', 'refused'].includes(s.status)) return null
  const why = s.message ?? s.error ?? null
  const code = s.code ?? s.error_code ?? null
  const text = why ?? code ?? 'no reason given'
  return `${s.status === 'refused' ? 'The case was refused' : 'The run failed'}: ${text}`
    + (why && code && !why.includes(code) ? ` (${code})` : '')
}
