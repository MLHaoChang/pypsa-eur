// The data-centre create form's pure part (DC 22): defaults, hints, checks and
// the create body. The defaults are `dc_load_preset` in
// backend/project_templates/eh_templates.py (a backend test pins them).
// Field labels are the Backup and Power architecture sections' own words.
import type { DcArchitecture, DcBackupMode } from '../../api/campusElectrical'
import { ARCH_FIELD, ARCH_LABEL } from '../DcArchitectureSection'
import { BACKUP_MODE_FIELD, MODE_LABEL } from '../DcBackupSection'

export type DcTemplateId = 'eh_datacenter' | 'eh_datacenter_staged'
export const DC_TEMPLATES: readonly string[] = ['eh_datacenter', 'eh_datacenter_staged']

export type DcField = 'it_load' | 'pue' | 'backup' | 'architecture' | 'workload' | 'compute'
export const DC_FIELD_LABEL: Record<DcField, string> = {
  it_load: 'IT load', pue: 'PUE at design', backup: BACKUP_MODE_FIELD, architecture: ARCH_FIELD,
  workload: 'Workload', compute: 'Compute',
}
export const DC_ARCH_OPTIONS = Object.entries(ARCH_LABEL) as [DcArchitecture, string][]
export const DC_BACKUP_OPTIONS = Object.entries(MODE_LABEL) as [DcBackupMode, string][]

export const ARCH_HINT: Record<DcArchitecture, string> = {
  ac: 'Double-conversion UPS at low voltage, the common baseline. Offers no grid services.',
  mv_ups: 'UPS at medium voltage. Its own storage can offer voltage control and frequency response.',
  dc_800v: 'A central rectifier feeds an 800 VDC bus. Offers voltage control; frequency response needs storage on the DC bus.',
  sst: 'A solid-state transformer from medium-voltage AC to 800 VDC. Offers the most grid services, most of them with storage on the DC bus.',
}
export const BACKUP_HINT: Record<DcBackupMode, string> = {
  genset: 'The template’s generator fleet carries the critical load through a grid outage.',
  bess: 'Battery strings replace the gensets. They are sized at creation from the IT load and carry outages only, so the normal peak must fit the import.',
  hybrid: 'Gensets stay, and battery strings cover what the gensets leave uncovered.',
}
/** Under the hint on the staged example: at its default PUE the build plan's
 *  peak does not fit the import with Batteries alone, so Create refuses it
 *  (DC walkthrough 01). A low enough PUE fits, so the line names the default. */
export const STAGED_BESS_LINE = `Create refuses ${MODE_LABEL.bess} on this example at the default PUE: pick ${MODE_LABEL.hybrid}.`

/** The example's own defaults: single IT load (MW) or the staged phases. */
export const DC_DEFAULTS = {
  pue: 1.3, architecture: 'ac' as DcArchitecture, backup: 'genset' as DcBackupMode,
  itLoadMw: 36,
  /** The single-year example's week (SNAPSHOTS) and so its phase's start year. */
  singleYear: 2030,
  phases: [{ year: 2027, mw: 12 }, { year: 2030, mw: 24 }, { year: 2035, mw: 36 }],
}

/** The Workload select's plain words, keyed by the backend's `utilisation` values
 *  (a backend test holds the two lists equal). */
export type DcUtilisation = 'ai_training' | 'inference' | 'mixed' | 'flat'
export const UTILISATION_OPTIONS: [DcUtilisation, string][] = [
  ['ai_training', 'AI training'], ['inference', 'Inference'], ['mixed', 'Mixed'], ['flat', 'Flat'],
]
/** The assumed shape inputs, as the engine's defaults; shown as assumed, unit beside. */
export type MixKey = 'trough' | 'peak' | 'peakHour' | 'training' | 'flat'
export const MIX_FIELDS: Record<DcUtilisation, [MixKey, string, string][]> = {
  ai_training: [],
  inference: [['trough', 'Lowest load', 'share of IT MW (0 to 1)'], ['peak', 'Highest load', 'share of IT MW (0 to 1)'], ['peakHour', 'Peak hour', 'hour of day, 0 to 23']],
  mixed: [['training', 'Training share', 'share of IT MW (0 to 1)'], ['trough', 'Inference lowest load', 'share of IT MW (0 to 1)'], ['peak', 'Inference highest load', 'share of IT MW (0 to 1)'], ['peakHour', 'Inference peak hour', 'hour of day, 0 to 23']],
  flat: [['flat', 'Constant load', 'share of IT MW (0 to 1)']],
}
export const MIX_DEFAULTS: Record<MixKey, string> = { trough: '0.4', peak: '0.85', peakHour: '20', training: '0.5', flat: '0.8' }
export const MISMATCH_NOTE_ABOVE = 0.1

export interface ComputeDraft { counts: string[]; kw: string; util: string }
export const emptyCompute = (staged: boolean): ComputeDraft => ({ counts: staged ? ['', '', ''] : [''], kw: '', util: '' })
const computeUsed = (c: ComputeDraft) => c.kw.trim() !== '' || c.util.trim() !== '' || c.counts.some(x => x.trim() !== '')
/** IT MW from accelerators × kW each (host included) × utilisation; NaN until the three are numbers. */
export const derivedMw = (c: ComputeDraft, i: number) => {
  const n = asNumber(c.counts[i] ?? ''), kw = asNumber(c.kw), u = asNumber(c.util)
  return n * kw * u / 1000
}
/** The mismatch note (muted, not an error), naming both figures; null within 10 %. */
export function computeMismatch(derived: number, typed: number): string | null {
  if (!(Number.isFinite(derived) && typed > 0) || Math.abs(derived - typed) / typed <= MISMATCH_NOTE_ABOVE) return null
  return `Compute gives ${derived.toFixed(1)} MW; the typed IT load is ${typed.toFixed(1)} MW and is the one used.`
}

export interface DcDraft {
  it_load: string; pue: string; backup: DcBackupMode; architecture: DcArchitecture
  utilisation: DcUtilisation; mix: Record<MixKey, string>; compute: ComputeDraft
}
export const initialDcDraft = (staged = false): DcDraft => ({
  utilisation: 'ai_training', mix: { ...MIX_DEFAULTS }, compute: emptyCompute(staged),
  it_load: String(DC_DEFAULTS.itLoadMw), pue: String(DC_DEFAULTS.pue),
  backup: DC_DEFAULTS.backup, architecture: DC_DEFAULTS.architecture,
})

/** The three ambient fields, blank = from the site's climate zone, else the default curve. */
export type AmbientDraft = { mean: string; seasonal: string; diurnal: string }
export const emptyAmbient = (): AmbientDraft => ({ mean: '', seasonal: '', diurnal: '' })
const AMBIENT_KEYS = [
  ['mean', 'ambient_annual_mean_c'], ['seasonal', 'ambient_seasonal_amplitude_k'],
  ['diurnal', 'ambient_diurnal_amplitude_k'],
] as const

/** Which temperature the run will use, in words, from the draft. */
export function ambientSourceText(lat: number | null, a: AmbientDraft): string {
  const rest = lat != null ? 'the site location' : 'the default curve'
  if (Object.values(a).every(v => v.trim() === '')) {
    return lat != null
      ? 'Ambient temperature: from the site location. A climate zone is chosen from its latitude. It is not weather data.'
      : 'Ambient temperature: the default temperate (Central European) curve. No site location is set. A hot site’s cooling is understated.'
  }
  return Object.values(a).every(v => v.trim() !== '')
    ? 'Ambient temperature: as you entered it.'
    : `Ambient temperature: your entries, the rest from ${rest}.`
}

/** Checks on the three ambient fields; a blank one is fine (it comes from the site or default). */
export function validateAmbient(a: AmbientDraft): Partial<Record<keyof AmbientDraft, string>> {
  const e: Partial<Record<keyof AmbientDraft, string>> = {}
  const check = (k: keyof AmbientDraft, label: string, lo: number, hi: number, unit: string) => {
    const raw = a[k].trim()
    if (raw === '') return
    const v = Number(raw)
    if (!Number.isFinite(v) || v < lo || v > hi) e[k] = `${label}: enter a number from ${lo} to ${hi} ${unit}, or clear it`
  }
  check('mean', 'Annual mean', -60, 60, '°C')
  check('seasonal', 'Seasonal swing', 0, 40, '°C')
  check('diurnal', 'Daily swing', 0, 30, '°C')
  return e
}

const asNumber = (s: string) => (s.trim() === '' ? NaN : Number(s))

/** Checks beside each field, in the field label's words; the staged plan's
 *  phases are not typed here, so it has no IT load check. */
export function validateDc(d: DcDraft, templateId: string): Partial<Record<DcField, string>> {
  const e: Partial<Record<DcField, string>> = {}
  const mw = asNumber(d.it_load)
  if (templateId !== 'eh_datacenter_staged' && !(Number.isFinite(mw) && mw > 0)) {
    e.it_load = `${DC_FIELD_LABEL.it_load}: enter a number above 0 (MW)`
  }
  const pue = asNumber(d.pue)
  if (!(Number.isFinite(pue) && pue >= 1)) e.pue = `${DC_FIELD_LABEL.pue}: enter a number of at least 1`
  for (const [key] of MIX_FIELDS[d.utilisation]) {
    const v = asNumber(d.mix[key])
    const ok = key === 'peakHour' ? Number.isInteger(v) && v >= 0 && v <= 23 : Number.isFinite(v) && v >= 0 && v <= 1
    if (!ok) { e.workload = `${DC_FIELD_LABEL.workload}: ${key === 'peakHour' ? 'the peak hour is a whole hour from 0 to 23' : 'shares are numbers from 0 to 1'}`; break }
  }
  if (d.utilisation === 'inference' || d.utilisation === 'mixed') {
    if (asNumber(d.mix.trough) > asNumber(d.mix.peak)) e.workload = `${DC_FIELD_LABEL.workload}: the lowest load must not exceed the highest`
  }
  if (computeUsed(d.compute)) {
    const bad = d.compute.counts.some(c => !(asNumber(c) > 0)) || !(asNumber(d.compute.kw) > 0)
      || !(asNumber(d.compute.util) > 0 && asNumber(d.compute.util) <= 1)
    if (bad) e.compute = `${DC_FIELD_LABEL.compute}: give every accelerator count and the kW above 0, and a utilisation above 0 up to 1, or clear them all`
  }
  return e
}

/** The create body's `dc_load`. The staged plan keeps its own phases. */
export function dcLoadBody(
  d: DcDraft, templateId: string, site?: { lat: number | null; ambient: AmbientDraft },
): Record<string, unknown> {
  const dc: Record<string, unknown> = {
    pue_at_design: Number(d.pue), architecture: d.architecture, backup: { mode: d.backup },
  }
  // The site latitude picks the ambient climate zone; a stated ambient field wins.
  if (site?.lat != null) dc.site_latitude = site.lat
  for (const [k, key] of AMBIENT_KEYS) {
    const raw = site?.ambient[k].trim() ?? ''
    if (raw !== '' && Number.isFinite(Number(raw))) dc[key] = Number(raw)
  }
  if (d.utilisation !== 'ai_training') {
    dc.utilisation = d.utilisation
    dc.workload_mix = {
      trough_share: Number(d.mix.trough), peak_share: Number(d.mix.peak), peak_hour: Number(d.mix.peakHour),
      training_share: Number(d.mix.training), flat_share: Number(d.mix.flat),
    }
  }
  if (computeUsed(d.compute)) {
    dc.compute = { kw_per_accelerator: Number(d.compute.kw), utilisation: Number(d.compute.util),
      accelerator_counts: d.compute.counts.map(Number) }
  }
  if (templateId !== 'eh_datacenter_staged') {
    dc.phases = [{ cod: `${DC_DEFAULTS.singleYear}-01-01`, it_mw: Number(d.it_load) }]
  }
  return { dc_load: dc }
}

/** The form field a backend `dc_load.*` field key belongs to, or null. */
export function dcFieldOf(key: string): DcField | null {
  if (key.startsWith('dc_load.phases')) return 'it_load'
  if (key === 'dc_load.pue_at_design') return 'pue'
  if (key.startsWith('dc_load.backup')) return 'backup'
  if (key === 'dc_load.architecture') return 'architecture'
  if (key.startsWith('dc_load.utilisation') || key.startsWith('dc_load.workload_mix')) return 'workload'
  if (key.startsWith('dc_load.compute')) return 'compute'
  return null
}

/** A refusal opening with the field label, never the key ("PUE at design: …"). */
export function dcRefusalText(field: DcField | null, message: string): string {
  const body = message.replace(/^dc_load[\w.]*:\s*/, '').replace(/^PUE:\s*/, '')
  return field ? `${DC_FIELD_LABEL[field]}: ${body}` : body
}
