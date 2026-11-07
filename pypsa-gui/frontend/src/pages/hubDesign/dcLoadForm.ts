// The data-centre create form's pure part (DC 22): defaults, hints, checks and
// the create body. The defaults are `dc_load_preset` in
// backend/project_templates/eh_templates.py (a backend test pins them).
// Field labels are the Backup and Power architecture sections' own words.
import type { DcArchitecture, DcBackupMode } from '../../api/campusElectrical'
import { ARCH_FIELD, ARCH_LABEL } from '../DcArchitectureSection'
import { BACKUP_MODE_FIELD, MODE_LABEL } from '../DcBackupSection'

export type DcTemplateId = 'eh_datacenter' | 'eh_datacenter_staged'
export const DC_TEMPLATES: readonly string[] = ['eh_datacenter', 'eh_datacenter_staged']

export type DcField = 'it_load' | 'pue' | 'backup' | 'architecture'
export const DC_FIELD_LABEL: Record<DcField, string> = {
  it_load: 'IT load', pue: 'PUE at design', backup: BACKUP_MODE_FIELD, architecture: ARCH_FIELD,
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
  bess: 'Battery strings replace the gensets. They are sized later, in the Backup section.',
  hybrid: 'Gensets stay, and battery strings cover what the gensets leave uncovered.',
}

/** The example's own defaults: single IT load (MW) or the staged phases. */
export const DC_DEFAULTS = {
  pue: 1.3, architecture: 'ac' as DcArchitecture, backup: 'genset' as DcBackupMode,
  itLoadMw: 36,
  /** The single-year example's week (SNAPSHOTS) and so its phase's start year. */
  singleYear: 2030,
  phases: [{ year: 2027, mw: 12 }, { year: 2030, mw: 24 }, { year: 2035, mw: 36 }],
}

export interface DcDraft { it_load: string; pue: string; backup: DcBackupMode; architecture: DcArchitecture }
export const initialDcDraft = (): DcDraft => ({
  it_load: String(DC_DEFAULTS.itLoadMw), pue: String(DC_DEFAULTS.pue),
  backup: DC_DEFAULTS.backup, architecture: DC_DEFAULTS.architecture,
})

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
  return e
}

/** The create body's `dc_load`. The staged plan keeps its own phases. */
export function dcLoadBody(d: DcDraft, templateId: string): Record<string, unknown> {
  const dc: Record<string, unknown> = {
    pue_at_design: Number(d.pue), architecture: d.architecture, backup: { mode: d.backup },
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
  return null
}

/** A refusal opening with the field label, never the key ("PUE at design: …"). */
export function dcRefusalText(field: DcField | null, message: string): string {
  const body = message.replace(/^dc_load[\w.]*:\s*/, '').replace(/^PUE:\s*/, '')
  return field ? `${DC_FIELD_LABEL[field]}: ${body}` : body
}
