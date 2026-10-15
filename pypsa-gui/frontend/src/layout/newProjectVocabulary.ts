// One vocabulary for the ways to create a project (UX overhaul S7, finding
// P2). The projects page cards and the new-project wizard tabs read from
// here, so the same action never has two names.
import type { NewProjectTab } from './NewProjectWizard'

export const CREATE_LABEL: Record<NewProjectTab, string> = {
  blank: 'Blank',
  template: 'From template',
  file: 'Import file',
  clone: 'Duplicate',
  study: 'Study',
  decision: 'Decision study',
  folder: 'Import folder',
}

/** Why a decision study cannot start here when accounts are on (P4). */
export const DECISION_DESKTOP_ONLY = 'Desktop app only for now.'
