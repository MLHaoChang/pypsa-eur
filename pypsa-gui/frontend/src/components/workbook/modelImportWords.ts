// The model-file import preview's words (project-io 04, RAW-NAMES-1): plain names
// only; the same headings the glossary defines (CONTEXT.md "Import preview").
import type { HEADINGS } from './workbookWords'

export const MODEL_HEADINGS: Record<keyof typeof HEADINGS, string> = {
  holds: 'What it holds',
  unmapped: 'Not carried',
  assumed: 'Assumed',
  warnings: 'Warnings',
  suggestions: 'Suggested (not applied)',
  rederived: 'Costs worked out again',
  notCarried: 'Not carried',
}
