// The generic site form's logic (generic-site E2E, G2), kept pure so it runs
// under vitest's node env: the values a site type's preset gives, the values
// that follow other values until the user sets them (the UPS, the
// energization, the fault level), the checks the backend applies
// (project_templates/eh_site.py::site_params) said in plain words beside the
// field, and the request body, which carries only what the user set so the
// template metadata's `entered` list can tell the two apart.
//
// Field keys are the backend's parameter paths ("stages[1].peak_mw"), so a
// server refusal (422 {code: "site_params_invalid", field, message}) lands on
// the same field as the client's own check.

export type SiteTypeId = 'industrial' | 'commercial' | 'hospital' | 'campus' | 'other'

export interface SiteStage { year: number; peak_mw: number }
export interface SiteParams {
  site_type: SiteTypeId
  name: string | null
  stages: SiteStage[]
  load_profile: string
  critical_share: number
  connection_mw: number
  energization: { year: number; mw: number }[]
  fault_level_mva: number
  horizon_end: number
  existing: {
    gensets: { count: number; unit_mw: number; fuel: 'gas' | 'diesel' }
    pv_mw: number
    battery: { mw: number; mwh: number }
    ups: { mw: number; minutes: number }
  }
  candidates: Record<'genset' | 'bess' | 'pv', { enabled: boolean; max_mw: number }>
}

export interface SiteForm {
  site_types: { id: SiteTypeId; label: string; example: string }[]
  presets: Record<SiteTypeId, SiteParams>
  load_profiles: { id: string; label: string }[]
  bounds: {
    max_stages: number; year_min: number; year_max: number; peak_mw_max: number
    connection_mw_max: number; max_gensets: number; unit_mw_max: number
    ups_minutes_min: number; ups_minutes_max: number; horizon_end: number
  }
}

/** The form's state: the current values and the paths the user has set. */
export interface SiteDraft { values: SiteParams; touched: string[] }

// structuredClone, not a JSON round trip: a cleared field is NaN, and JSON
// would turn it into null, which the derived defaults then refill.
const clone = <T,>(x: T): T => structuredClone(x)

function getPath(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown> | null)?.[k], obj)
}

function setPath(obj: Record<string, unknown>, path: string, v: unknown): void {
  const keys = path.split('.')
  let o = obj
  for (const k of keys.slice(0, -1)) {
    if (typeof o[k] !== 'object' || o[k] === null) o[k] = {}
    o = o[k] as Record<string, unknown>
  }
  o[keys[keys.length - 1]] = v
}

/** Lists are one value: `stages`, `energization`. */
const LIST_PATHS = ['stages', 'energization']

/** The path a field key is recorded under ("stages[1].year" → "stages"). */
export function touchPath(field: string): string {
  const head = field.split(/[[.]/)[0]
  return LIST_PATHS.includes(head) ? head : field
}

export function isDefault(draft: SiteDraft, field: string): boolean {
  return !draft.touched.includes(touchPath(field))
}

const round3 = (x: number) => Math.round(x * 1000) / 1000

/** Fill the values that follow others while the user has not set them, as
 *  the backend does: the UPS carries the critical load with 10 % margin, the
 *  energization is the connection in every stage, the fault level 20 × it. */
export function derive(draft: SiteDraft): SiteDraft {
  const v = clone(draft.values)
  const t = new Set(draft.touched)
  const last = v.stages[v.stages.length - 1]?.peak_mw ?? 0
  // A blank field the derived value reads keeps the last good value, so the
  // problem shows on the field the user is editing, not on one they never set.
  const ups = round3(1.1 * v.critical_share * last)
  if (!t.has('existing.ups.mw') && Number.isFinite(ups)) v.existing.ups.mw = ups
  if (!t.has('fault_level_mva') && Number.isFinite(v.connection_mw)) {
    v.fault_level_mva = 20 * v.connection_mw
  }
  if (!t.has('energization')) {
    v.energization = v.stages.map(s => ({ year: s.year, mw: v.connection_mw }))
  } else {
    // A stage added or moved keeps the user's figure for a year it already
    // had, and starts a new year at the connection.
    const byYear = new Map(v.energization.map(e => [e.year, e.mw]))
    const byIndex = v.energization.map(e => e.mw)
    v.energization = v.stages.map((s, i) => ({
      year: s.year,
      mw: byYear.get(s.year) ?? byIndex[i] ?? v.connection_mw,
    }))
  }
  return { values: v, touched: draft.touched }
}

export function initialDraft(form: SiteForm, type: SiteTypeId = 'industrial'): SiteDraft {
  return derive({ values: clone(form.presets[type]), touched: [] })
}

/** Switch the site type: every field the user has not set takes the new
 *  preset's value; the ones they set stay. */
export function withSiteType(form: SiteForm, draft: SiteDraft, type: SiteTypeId): SiteDraft {
  const values = clone(form.presets[type]) as unknown as Record<string, unknown>
  for (const p of draft.touched) setPath(values, p, clone(getPath(draft.values, p)))
  ;(values as unknown as SiteParams).site_type = type
  return derive({ values: values as unknown as SiteParams, touched: draft.touched })
}

/** Set one field (a parameter path, or a list path with the whole list). */
export function setField(draft: SiteDraft, path: string, value: unknown): SiteDraft {
  const values = clone(draft.values) as unknown as Record<string, unknown>
  setPath(values, path, value)
  const p = touchPath(path)
  const touched = draft.touched.includes(p) ? draft.touched : [...draft.touched, p]
  return derive({ values: values as unknown as SiteParams, touched })
}

export function setStage(draft: SiteDraft, i: number, key: keyof SiteStage, value: number): SiteDraft {
  const stages = draft.values.stages.map((s, j) => (j === i ? { ...s, [key]: value } : s))
  return setField(draft, 'stages', stages)
}

export function addStage(draft: SiteDraft, bounds: SiteForm['bounds']): SiteDraft {
  const st = draft.values.stages
  if (st.length >= bounds.max_stages) return draft
  const last = st[st.length - 1]
  const year = Math.min(last.year + 3, draft.values.horizon_end - 1)
  return setField(draft, 'stages', [...st, { year, peak_mw: last.peak_mw }])
}

export function removeStage(draft: SiteDraft, i: number): SiteDraft {
  if (draft.values.stages.length <= 1) return draft
  return setField(draft, 'stages', draft.values.stages.filter((_, j) => j !== i))
}

export function setEnergization(draft: SiteDraft, i: number, mw: number): SiteDraft {
  const energization = draft.values.energization.map((e, j) => (j === i ? { ...e, mw } : e))
  return setField(draft, 'energization', energization)
}

const fmt = (x: number) => `${Number(x.toPrecision(6))}`

/** The checks the backend applies, in plain words, keyed by field. */
export function validate(draft: SiteDraft, bounds: SiteForm['bounds']): Record<string, string> {
  const v = draft.values
  const err: Record<string, string> = {}
  const num = (key: string, x: number, lo: number, hi: number, open = false) => {
    if (!Number.isFinite(x)) err[key] = 'Enter a number.'
    else if (open ? x <= lo : x < lo) err[key] = open ? `Must be above ${fmt(lo)}.` : `Must be at least ${fmt(lo)}.`
    else if (x > hi) err[key] = `Must be at most ${fmt(hi)}.`
  }
  v.stages.forEach((s, i) => {
    const key = `stages[${i}].year`
    if (!Number.isInteger(s.year) || s.year < bounds.year_min || s.year > bounds.year_max) {
      err[key] = `A year from ${bounds.year_min} to ${bounds.year_max}.`
    } else if (i > 0 && s.year <= v.stages[i - 1].year) {
      err[key] = `${s.year} must come after ${v.stages[i - 1].year}.`
    } else if (s.year >= v.horizon_end) {
      err[key] = `Must be before ${v.horizon_end}, when the study ends.`
    }
    num(`stages[${i}].peak_mw`, s.peak_mw, 0, bounds.peak_mw_max, true)
  })
  num('connection_mw', v.connection_mw, 0, bounds.connection_mw_max, true)
  num('fault_level_mva', v.fault_level_mva, 0, Infinity, true)
  v.energization.forEach((e, i) => num(`energization[${i}].mw`, e.mw, 0, v.connection_mw))
  // The grid-only comparison must run under its own connection (owner, Q1).
  v.stages.forEach((s, i) => {
    const key = `stages[${i}].peak_mw`
    const e = v.energization[i]?.mw
    if (!err[key] && Number.isFinite(e) && s.peak_mw > e + 1e-9) {
      err[key] = `Peak load in ${s.year} (${fmt(s.peak_mw)} MW) is above the ${fmt(e)} MW `
        + 'available from the grid that year. Raise the connection or lower the peak.'
    }
  })
  num('critical_share', v.critical_share, 0, 1, true)
  const g = v.existing.gensets
  if (!Number.isInteger(g.count) || g.count < 0 || g.count > bounds.max_gensets) {
    err['existing.gensets.count'] = `A whole number from 0 to ${bounds.max_gensets}.`
  }
  num('existing.gensets.unit_mw', g.unit_mw, 0, bounds.unit_mw_max, true)
  num('existing.pv_mw', v.existing.pv_mw, 0, Infinity)
  num('existing.battery.mw', v.existing.battery.mw, 0, Infinity)
  num('existing.battery.mwh', v.existing.battery.mwh, 0, Infinity)
  if (!err['existing.battery.mwh'] && !err['existing.battery.mw']
      && (v.existing.battery.mw > 0) !== (v.existing.battery.mwh > 0)) {
    err['existing.battery.mwh'] = 'Give both the power and the energy, or neither.'
  }
  num('existing.ups.mw', v.existing.ups.mw, 0, Infinity)
  num('existing.ups.minutes', v.existing.ups.minutes, 0, bounds.ups_minutes_max)
  const last = v.stages[v.stages.length - 1]
  const critical = v.critical_share * (last?.peak_mw ?? 0)
  if (!err['existing.ups.mw'] && v.existing.ups.mw > 0 && v.existing.ups.mw < critical - 1e-9) {
    err['existing.ups.mw'] = `The UPS carries the must-stay-on load it protects, `
      + `${fmt(critical)} MW in ${last.year}.`
  }
  if (!err['existing.ups.minutes'] && v.existing.ups.mw > 0
      && v.existing.ups.minutes < bounds.ups_minutes_min) {
    err['existing.ups.minutes'] = `At least ${fmt(bounds.ups_minutes_min)} min, until the gensets carry the load.`
  }
  for (const k of ['genset', 'bess', 'pv'] as const) {
    num(`candidates.${k}.max_mw`, v.candidates[k].max_mw, 0, Infinity)
  }
  return err
}

/** The request body: the site type and only the values the user set. */
export function requestBody(draft: SiteDraft): Record<string, unknown> {
  const body: Record<string, unknown> = { site_type: draft.values.site_type }
  for (const p of draft.touched) setPath(body, p, clone(getPath(draft.values, p)))
  return body
}

/** One line previewing what the site type sets (the record is the per-field
 *  "default" tag; this is only the preview). Figures rounded as the tiles. */
export function presetSummary(p: SiteParams, figure: (x: number) => string): string {
  const peaks = p.stages.map(s => figure(s.peak_mw)).join(' → ')
  const g = p.existing.gensets
  const parts = [
    `Peak ${peaks} MW`,
    `${figure(p.connection_mw)} MW connection`,
    g.count ? `${g.count} ${g.fuel} genset${g.count > 1 ? 's' : ''} of ${figure(g.unit_mw)} MW` : 'no gensets',
  ]
  if (p.existing.pv_mw > 0) parts.push(`${figure(p.existing.pv_mw)} MW solar PV`)
  if (p.existing.battery.mw > 0) parts.push(`a ${figure(p.existing.battery.mw)} MW battery`)
  if (p.existing.ups.mw > 0) parts.push('a UPS on the must-stay-on load')
  return `${parts.join(', ')}.`
}

/** The first field with a problem, in the order the form shows them. */
export const FIELD_ORDER = [
  'stages', 'load_profile', 'critical_share', 'connection_mw', 'energization', 'fault_level_mva',
  'existing.gensets', 'existing.pv_mw', 'existing.battery', 'existing.ups', 'candidates',
]
export function firstErrorField(errors: Record<string, string>): string | null {
  const keys = Object.keys(errors)
  for (const head of FIELD_ORDER) {
    const k = keys.find(x => x.startsWith(head))
    if (k) return k
  }
  return keys[0] ?? null
}
