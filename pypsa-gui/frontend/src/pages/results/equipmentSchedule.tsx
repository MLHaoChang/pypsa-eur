// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// The equipment schedule's shared words and chips: Results › Hand-off › Equipment schedule
// and the Short-circuit section's "Switchgear rating chosen" table read the same map,
// key and scroll region. Kinds and criteria come in plain words from the backend.

import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { campusApi, type EquipmentFlag, type EquipmentScheduleRow } from '../../api/campusElectrical'
import { Tag } from '../../components/PageKit'

export const EQUIPMENT_SCHEDULE_KEY = (project: string) => ['campusElectrical', 'equipment_schedule', project] as const

/** The project's equipment schedule (one query for both tables). */
export function useEquipmentSchedule(project: string | null | undefined) {
  return useQuery({
    queryKey: EQUIPMENT_SCHEDULE_KEY(project ?? ''),
    queryFn: () => campusApi.equipmentSchedule(project as string),
    enabled: !!project, retry: false,
  })
}

/** A figure as written in the table: at most two decimals, no trailing zeros. */
export function fig(v: number): string {
  return v.toLocaleString('en', { maximumFractionDigits: 2 })
}

const withUnit = (v: number, unit: string | null) => (unit ? `${fig(v)} ${unit}` : fig(v))

/** Every flag in words: the one map (flag key -> words). `warn` flags show the warning chip;
 *  the others are plain notes, as text. */
export const FLAG_WORDS: Record<EquipmentFlag, { warn: boolean; words: (r: EquipmentScheduleRow) => string }> = {
  declared_below_chosen: {
    warn: true,
    words: r => (r.declared_rating != null && r.rating != null
      ? `Declared ${withUnit(r.declared_rating, r.declared_unit ?? r.unit)} is below the chosen ${withUnit(r.rating, r.unit)}`
      : 'The declared rating is below the chosen one'),
  },
  beyond_ladder: { warn: true, words: () => 'Above the largest standard rating' },
  no_library_cable: { warn: true, words: () => 'No cable in the library is large enough' },
  not_evaluated: { warn: false, words: () => 'Not evaluated: no fault level was computed here' },
  not_rated: { warn: false, words: () => 'Not rated: no load flow reached this cable' },
  withstand_not_evaluated: { warn: false, words: () => 'Short-circuit withstand not evaluated' },
  partly_evaluated: { warn: false, words: () => 'Rated on the periods with a fault level only' },
}

/** A row's flags: the backend joins several with "; ", most severe first. */
export function flagsOf(r: Pick<EquipmentScheduleRow, 'flag'>): string[] {
  return (r.flag ?? '').split(';').map(f => f.trim()).filter(Boolean)
}

/** The source chips, one style each (keyed in `ChipKey`). */
const SOURCE: Record<EquipmentScheduleRow['source'], { tone: 'accent' | 'purple' | 'neutral'; label: string; means: string }> = {
  computed: { tone: 'accent', label: 'Computed', means: 'chosen by the study' },
  declared: { tone: 'purple', label: 'Declared', means: 'typed into the campus file' },
  assumed: { tone: 'neutral', label: 'Assumed', means: 'a value the study does not know' },
  'not sized': { tone: 'neutral', label: 'Not sized', means: 'the study cannot size it yet' },
}

export function SourceChip({ source }: { source: EquipmentScheduleRow['source'] }) {
  const s = SOURCE[source] ?? SOURCE.assumed
  return <Tag tone={s.tone}>{s.label}</Tag>
}

/** The warning chip: warn colour, in words, never the error red. */
export function WarnChip({ children }: { children: ReactNode }) {
  return (
    <span data-testid="schedule-warn-chip"
      className="inline-flex items-center px-1.5 py-[1px] rounded text-[10px] font-medium border bg-warn/10 text-warn border-warn/20">
      {children}
    </span>
  )
}

/** A note chip: something not evaluated, in words (neutral, not a warning). */
export function NoteChip({ children }: { children: ReactNode }) {
  return (
    <span data-testid="schedule-note-chip"
      className="inline-flex items-center px-1.5 py-[1px] rounded text-[10px] border bg-panel text-muted border-border">
      {children}
    </span>
  )
}

/** The Flag cell: one chip per flag (warning or note), in words, or "None". */
export function FlagCell({ row }: { row: EquipmentScheduleRow }) {
  const flags = flagsOf(row)
  if (flags.length === 0) return <span className="text-muted">None</span>
  return (
    <span className="inline-flex flex-col items-start gap-1">
      {flags.map(k => {
        const f = FLAG_WORDS[k as EquipmentFlag]
        if (!f) return <NoteChip key={k}>{cap(k.replace(/_/g, ' '))}</NoteChip>
        return f.warn ? <WarnChip key={k}>{f.words(row)}</WarnChip> : <NoteChip key={k}>{f.words(row)}</NoteChip>
      })}
    </span>
  )
}

/** The key to every chip style the tables use, shown once above a table. */
export function ChipKey({ sources = ['computed', 'declared', 'assumed', 'not sized'] }: {
  sources?: EquipmentScheduleRow['source'][]
}) {
  return (
    <div data-testid="schedule-chip-key" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted mb-2">
      {sources.map(s => (
        <span key={s} className="inline-flex items-center gap-1"><SourceChip source={s} /> {SOURCE[s].means}</span>
      ))}
      <span className="inline-flex items-center gap-1"><WarnChip>Warning</WarnChip> a rating to check, said in words (a declared rating below the chosen one, or none large enough)</span>
      <span className="inline-flex items-center gap-1"><NoteChip>Note</NoteChip> what the study could not evaluate</span>
    </div>
  )
}

/** A table region of fixed height that scrolls past `rows` rows, its header sticky. */
export function ScheduleScroll({ rows, children, testId }: { rows: number; children: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="overflow-auto border border-border rounded"
      style={{ maxHeight: `calc(${rows} * 1.9rem + 2rem)` }}>
      {children}
    </div>
  )
}

export const TH = 'sticky top-0 z-[1] bg-bg-2 font-normal text-left text-muted px-2 py-1 whitespace-nowrap'

/** A sentence's first letter in capitals (the backend's criteria start lower case). */
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** The Rating cell's words when there is no figure. */
export function missingRating(r: EquipmentScheduleRow): string {
  if (r.source === 'not sized') return 'Not sized yet'
  const flags = flagsOf(r)
  if (flags.includes('beyond_ladder') || flags.includes('no_library_cable')) return 'None large enough'
  return flags.includes('not_rated') ? 'Not rated' : 'Not evaluated'
}

/** The rating as written: figure and unit, or the reason there is none. */
export function ratingText(r: EquipmentScheduleRow): string {
  return r.rating == null ? missingRating(r) : withUnit(r.rating, r.unit)
}

/** The Declared cell: a declared figure, the rating itself for a declared asset, or why there is none. */
export function declaredText(r: EquipmentScheduleRow): string {
  if (r.declared_rating != null) return withUnit(r.declared_rating, r.declared_unit ?? r.unit)
  if (r.source === 'declared' && r.rating != null) return withUnit(r.rating, r.unit)
  return 'Not declared'
}
