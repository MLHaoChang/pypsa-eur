// Results grouped by the question each answers (UX overhaul S3; owner
// decisions D1a and D2a, docs/superpowers/plans/2026-10-14-ux-overhaul.md §2).
// Presentation only: the tab ids are unchanged, so deep links, stored tabs and
// the assistant's results_tab (RESULTS_TAB_ENUM) keep working. Asset detail is
// an inspector for one asset, not a result category, so it belongs to no group.

export type ResultsTab =
  | 'summary' | 'overview' | 'capex' | 'dispatch' | 'loadflow' | 'prices' | 'emissions'
  | 'economics' | 'curtailment' | 'lostload' | 'adequacy' | 'storage'
  | 'asset' | 'fmea' | 'investment'

export type ResultsGroup = 'summary' | 'investment' | 'grid_plan' | 'reliability' | 'operation'

export const RESULT_GROUPS: ReadonlyArray<{
  id: ResultsGroup
  label: string
  /** The question the group answers, shown as its tooltip. */
  question: string
  tabs: readonly ResultsTab[]
}> = [
  { id: 'summary', label: 'Summary', question: 'The solve at a glance', tabs: ['summary', 'overview'] },
  { id: 'investment', label: 'Investment', question: 'What to build, and what it costs and earns',
    tabs: ['capex', 'economics', 'prices', 'investment'] },
  { id: 'grid_plan', label: 'Grid plan', question: 'Whether the network and the connection carry it',
    tabs: ['loadflow'] },
  { id: 'reliability', label: 'Reliability', question: 'Whether the critical load stays on',
    tabs: ['adequacy', 'lostload', 'fmea'] },
  { id: 'operation', label: 'Operation', question: 'How it runs hour by hour',
    tabs: ['dispatch', 'storage', 'curtailment', 'emissions'] },
]

export function groupOf(tab: ResultsTab): ResultsGroup | null {
  return RESULT_GROUPS.find(g => g.tabs.includes(tab))?.id ?? null
}
