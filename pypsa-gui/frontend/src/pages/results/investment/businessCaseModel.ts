// The business-case line table's rules (business-case 09, spec §4): the units a
// line may take from its quantity source, where each refusal sits (G9), the
// per-year paste, and the plain words the table shows. The lists (categories,
// escalations, typed units, linked outputs) come from the options route, so
// nothing here repeats the backend's closed lists.
import type {
  BusinessCaseOptions, BusinessCaseRefusal, LineItem, LineKind, LinkedOutput,
} from '../../../api/businessCase'

export type SourceKind = 'typed' | 'linked' | 'share_of'

export const sourceKind = (qs: string | undefined): SourceKind =>
  qs?.startsWith('linked:') ? 'linked' : qs?.startsWith('share_of:') ? 'share_of' : 'typed'

/** `linked:<output id>@<version>` → the output id. */
export const linkedOutputId = (qs: string): string =>
  qs.slice('linked:'.length).replace(/@[^@]*$/, '')

export const linkedSource = (id: string, version: string) => `linked:${id}@${version}`

export function outputOf(line: LineItem, opts: BusinessCaseOptions | null): LinkedOutput | null {
  if (sourceKind(line.quantity_source) !== 'linked') return null
  const id = linkedOutputId(line.quantity_source)
  return opts?.outputs?.find(o => o.id === id) ?? null
}

/** The units the backend allows for the line's source and kind (`units_by_source`,
 *  the backend's `allowed_units`); null when the options do not say (not loaded, or a
 *  linked output this model does not offer). */
function allowedFor(line: LineItem, opts: BusinessCaseOptions | null): string[] | null {
  const by = opts?.units_by_source
  if (!by) return null
  const src = sourceKind(line.quantity_source)
  if (src === 'linked') return by.linked[linkedOutputId(line.quantity_source)]?.[line.kind] ?? null
  return by[src][line.kind] ?? null
}

/** The units the Unit select offers (spec §4 "Units by source"), as the backend
 *  serves them. The line's own unit is kept in the list when it is not allowed, so
 *  the refusal shows on a value the user can see. */
export function unitsFor(line: LineItem, opts: BusinessCaseOptions | null): string[] {
  const allowed = allowedFor(line, opts) ?? []
  return allowed.includes(line.unit) || !line.unit ? allowed : [...allowed, line.unit]
}

/** Is the line's current unit outside what its source allows? */
export const unitNotAllowed = (line: LineItem, opts: BusinessCaseOptions | null): boolean => {
  const allowed = allowedFor(line, opts)
  return allowed !== null && !allowed.includes(line.unit)
}

/** Units in words (UX: "USD per GPU-hour", never "USD/GPU_h"). */
export const UNIT_WORDS: Record<string, string> = {
  MWh: 'MWh', MWh_H2: 'MWh of hydrogen', kg: 'kg', t: 'tonne', GPU_h: 'GPU-hour',
  kW_month: 'kW-month', kW_year: 'kW-year', MW: 'MW', MW_year: 'MW-year', unit: 'unit',
  unit_month: 'unit-month', lump: 'lump sum', share: 'share',
}
export const unitWords = (u: string) => UNIT_WORDS[u] ?? u.replace(/_/g, ' ')

/** "USD per GPU-hour"; a lump sum is the currency alone; a unit-free price reads "per <unit>". */
export function priceUnitWords(currency: string | null, unit: string): string {
  if (unit === 'lump') return currency ? `${currency}, lump sum` : 'lump sum'
  return `${currency ?? ''} per ${unitWords(unit)}`.trim()
}

/** A hint's "USD/GPU_h" in words. */
export function hintUnitWords(unit: string): string {
  const [cur, per] = unit.split('/', 2)
  return per === undefined ? unitWords(cur) : priceUnitWords(cur, per)
}

// ── refusal placement (G9) ──────────────────────────────────────────────────

export interface Placed { line: number; column: string; kind: string; message: string }

const FIELD_RE = /^lines\[(\d+)\]\.([a-z_]+)/

/** `lines[<i>].<column>` → {line, column}; anything else is case-level. */
export function parseField(field: string | undefined): { line: number; column: string } | null {
  const m = field ? FIELD_RE.exec(field) : null
  return m ? { line: Number(m[1]), column: m[2] } : null
}

/** Every refusal of the stored lines on its row and column. A refusal with no
 *  line (none today) is returned under line -1 so it still shows. */
export function placeRefusals(refusals: BusinessCaseRefusal[] | null | undefined): Placed[] {
  return (refusals ?? []).map(r => {
    const at = parseField(r.field)
    const kind = r.refusal ?? r.code
    return { line: at?.line ?? -1, column: at?.column ?? '', kind, message: refusalWords(kind, r.field) }
  })
}

/** A 422 validation error from the inputs PUT (`loc` from pydantic): the line
 *  and column it names, under `business_case.inputs.lines.<i>.<column>`. */
export function placeValidation(errors: { loc: string[]; msg: string }[] | undefined): Placed[] {
  return (errors ?? []).map(e => {
    const i = e.loc.indexOf('lines')
    const line = i >= 0 ? Number(e.loc[i + 1]) : -1
    const column = i >= 0 ? (e.loc[i + 2] ?? '') : ''
    const msg = e.msg.replace(/^Value error, /, '')
    const label = COLUMN_LABEL[column]
    return { line: Number.isFinite(line) ? line : -1, column, kind: 'invalid',
             message: label ? `${label}: ${msg}` : msg }
  })
}

export const COLUMN_LABEL: Record<string, string> = {
  name: 'Name',
  kind: 'Kind',
  category: 'Category',
  unit: 'Unit',
  quantity_source: 'Quantity from',
  quantity: 'Quantity',
  quantity_by_year: 'Quantity by year',
  price: 'Price',
  price_by_year: 'Price by year',
  money_year: 'Money year',
  escalation: 'Escalation',
  indexation: 'Indexation',
  start_year: 'Start year',
  end_year: 'End year',
  depreciation: 'Depreciation',
  life_years: 'Life (years)',
  rate: 'Rate',
  refresh_years: 'Refresh every (years)',
  note: 'Source note',
  linked_share: 'Sold share',
  linked_share_by_year: 'Sold share by year',
}

/** Plain words per refusal kind (G9), each opening with the column label. A
 *  backend test pins the keys to `REFUSAL_KINDS`. */
export const REFUSAL_WORDS: Record<string, string> = {
  name_duplicate: 'Name: another line has this name',
  category_not_in_kind: 'Category: not a category of this kind',
  unit_not_allowed: 'Unit: not offered for this quantity source',
  quantity_missing: 'Quantity: give a quantity or fill every year',
  share_out_of_range: 'Quantity: a share is between 0 and 1',
  share_cycle: 'Quantity from: a share cannot read itself or another share',
  share_base_empty: 'Quantity from: no line to take a share of',
  end_before_start: 'End year: before the start year',
  outside_horizon: 'Years: outside the investment case years',
  year_out_of_range: 'By year: a year outside the line’s start to end',
  indexation_missing: 'Indexation: needed with contract escalation',
  life_missing: 'Life (years): needed for capex',
  rate_missing: 'Rate: needed for declining balance',
  price_not_established: 'Price: not established, the run waits for it',
  cost_not_established: 'Price: not established and no default, the run waits for it',
  capex_missing: 'Category: this revenue needs a capex line of that category',
  it_capex_missing: 'Category: this revenue needs an IT hardware capex line',
  facility_capex_missing: 'Category: this revenue needs a facility capex line',
  capex_not_owned: 'Category: this preset’s project does not own that capex',
  pass_through_price_includes_energy: 'Category: the price already includes energy, so no cost recovery line',
  linked_output_missing: 'Quantity from: this model has no such output',
  double_count: 'Quantity from: the energy model rows already price this output',
  default_unit_mismatch: 'Price: the cost default is in another unit, not filled',
}

const COLUMN_OF_FIELD = (field: string | undefined) => parseField(field)?.column ?? ''

export function refusalWords(kind: string, field?: string): string {
  const base = kind.split(':', 1)[0]
  const words = REFUSAL_WORDS[base]
  if (words) return words
  const label = COLUMN_LABEL[COLUMN_OF_FIELD(field)]
  return label ? `${label}: refused` : 'Refused'
}

// ── per-year grid and paste (BCA-SCOPE-1 Q2) ────────────────────────────────

export function yearsOf(line: LineItem, horizon: [number, number] | null): number[] {
  const first = line.start_year ?? horizon?.[0]
  const last = line.end_year ?? horizon?.[1]
  if (first == null || last == null || last < first || last - first > 100) return []
  return Array.from({ length: last - first + 1 }, (_, i) => first + i)
}

export interface PasteResult {
  values: Record<string, number>
  /** Cells that are not numbers, by the year they would land on. */
  bad: number[]
  /** Pasted cells past the line's last year. */
  extra: number
}

/** Spreadsheet text (tabs and/or newlines) into the years from `fromYear` on.
 *  An empty cell clears that year; a cell that is not a number is reported, never
 *  read as 0. Thousands separators and spaces are removed. */
export function parsePaste(text: string, years: number[], fromYear: number): PasteResult {
  const cells = text.replace(/\r/g, '').replace(/\n$/, '').split(/[\t\n]/)
  const start = years.indexOf(fromYear)
  const out: PasteResult = { values: {}, bad: [], extra: 0 }
  if (start < 0) return out
  cells.forEach((raw, k) => {
    const year = years[start + k]
    if (year === undefined) { out.extra += 1; return }
    const t = raw.trim().replace(/[\s,]/g, '')
    if (t === '') return
    const n = Number(t)
    if (Number.isFinite(n)) out.values[String(year)] = n
    else out.bad.push(year)
  })
  return out
}

// ── provenance (spec §4) ────────────────────────────────────────────────────

export type Provenance = 'stated' | 'assumed'

export function provenanceOf(line: LineItem): Provenance {
  return line.assumed && Object.keys(line.assumed).length > 0 ? 'assumed' : 'stated'
}

/** "Assumed: <source>, <year>" for each defaulted column, in words. */
export function provenanceDetail(line: LineItem): string[] {
  return Object.entries(line.assumed ?? {}).map(([col, d]) =>
    `${COLUMN_LABEL[col] ?? col}: ${d!.source}, ${d!.source_year}`)
}

/** An edit to a defaulted column makes it stated: the column's `assumed` entry goes. */
export function edited(line: LineItem, patch: Partial<LineItem>): LineItem {
  const next: LineItem = { ...line, ...patch }
  const assumed = { ...(line.assumed ?? {}) }
  let changed = false
  for (const col of Object.keys(patch)) {
    if (col in assumed) { delete assumed[col as keyof typeof assumed]; changed = true }
  }
  if (changed) next.assumed = assumed
  return next
}

/** The kind's first category and a unit the source allows, when the kind changes. */
export function withKind(line: LineItem, kind: LineKind, opts: BusinessCaseOptions | null): LineItem {
  const cats = opts?.categories[kind] ?? []
  const next: LineItem = { ...line, kind, category: cats.includes(line.category) ? line.category : (cats[0] ?? '') }
  if (kind !== 'capex') {
    delete next.depreciation; delete next.life_years; delete next.rate; delete next.refresh_years
  }
  const units = unitsFor({ ...next, unit: '' }, opts)
  if (!units.includes(next.unit)) next.unit = units[0] ?? next.unit
  return next
}

export function blankLine(kind: LineKind, names: string[], opts: BusinessCaseOptions | null): LineItem {
  let n = 1
  while (names.includes(`New line ${n}`)) n += 1
  return withKind({
    name: `New line ${n}`, kind, category: '', unit: '', quantity_source: 'typed',
    escalation: kind === 'capex' ? 'capex' : 'opex',
  }, kind, opts)
}

/** A unique name for a copied row: "<label>", then "<label> 2", … */
export function uniqueName(base: string, names: string[]): string {
  if (!names.includes(base)) return base
  let n = 2
  while (names.includes(`${base} ${n}`)) n += 1
  return `${base} ${n}`
}

export const OUTPUTS_REASON_WORDS: Record<string, string> = {
  no_network: 'no model is loaded',
  no_current_solve: 'the model has no current solve; run the optimisation',
  outputs_not_readable: 'the model outputs cannot be read',
}

export const REFUSALS_REASON_WORDS: Record<string, string> = {
  investment_case_missing: 'Run the investment case to check the lines against its years.',
  investment_case_out_of_date: 'The investment case is out of date: run it again to check the lines against its years.',
  business_case_inputs_invalid: 'The stored lines do not read; fix the marked cells and save.',
}
