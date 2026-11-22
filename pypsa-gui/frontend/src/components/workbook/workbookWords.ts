// The Excel input workbook's words, shared by the Export chooser, the Start
// tile, the wizard and the import dialog (excel-input-workbook S3).
import type { SwitchResult } from '../../utils/projectActions'
import { money } from '../../utils/money'

export const INPUT_WORKBOOK_LABEL = 'Input workbook (Excel)'
export const INPUT_WORKBOOK_DESC = "This project's inputs on plain-named sheets, to edit in Excel and import as a new project"
export const TEMPLATE_LABEL = 'Blank input workbook (Excel)'
export const TEMPLATE_DESC = 'The same sheets, empty, with units and lists of allowed values'
/** Always beside the input workbook row (UX 27 Q3). */
export const AS_LAST_SAVED = "Holds the project as last saved: unsaved edits aren't included."
export const SAVE_FIRST_REASON = 'Save the project first.'

export const START_TILE_TITLE = 'From a filled workbook'
export const START_TILE_DESC = 'An Excel input workbook you filled in: see what it reads before the project is created.'

export const HEADINGS = {
  holds: 'What it holds',
  unmapped: 'Left out',
  assumed: 'Assumed',
  warnings: 'Warnings',
  suggestions: 'Suggestions',
  rederived: 'Costs worked out again',
  notCarried: 'Not in a workbook',
} as const

/** Lists fold after this many rows behind "(N more) Show all". */
export const SHOWN_AT_FIRST = 5

const isMoney = (parameter: string) => /€|EUR\b/.test(parameter)

/** An assumed value as the row shows it: money in the money() style, "no limit" for a blank. */
export function assumedValue(parameter: string, value: unknown): string {
  if (value === null || value === undefined) return 'no limit'
  if (typeof value === 'number') {
    if (isMoney(parameter)) return money(value, 'EUR')
    return value.toLocaleString('en', { maximumSignificantDigits: 6 })
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

/** The name the Import button would use, or why it cannot. */
export function nameProblem(name: string, taken: readonly string[]): string | null {
  const n = name.trim()
  if (!n) return 'Name the project first.'
  if (taken.includes(n)) return `A project named “${n}” already exists.`
  return null
}

/** "X 2", "X 3"… until free. */
export function freeName(name: string, taken: readonly string[], next: (n: string) => string): string {
  let candidate = next(name.trim())
  for (let i = 0; i < 50 && taken.includes(candidate); i++) candidate = next(candidate)
  return candidate
}

/** A created project is not opened while a study runs on the open one. */
export const STUDY_RUNNING_NOT_OPENED = 'a study is still running. Wait for it to finish or abort it, then try again.'

/** Why a created project was not opened, after "Created ‘X’, not opened: ". */
export function notOpenedReason(r: SwitchResult): string | null {
  switch (r.status) {
    case 'switched':
    case 'noop':
      return null
    case 'abort-failed': return 'the running simulation could not be stopped.'
    case 'busy-solve': return 'a solve is running. Finish or abort it, then try again.'
    case 'busy-study': return r.message
    case 'not-found': return 'the app could not find it.'
    case 'error': return 'the app could not open it.'
  }
}
