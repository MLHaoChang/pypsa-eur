// Results grouped by the question each answers (UX overhaul S3; owner
// decisions D1a and D2a, docs/superpowers/plans/2026-10-14-ux-overhaul.md §2).
// Presentation only: the tab ids are unchanged, so deep links, stored tabs and
// the assistant's results_tab (RESULTS_TAB_ENUM) keep working. Asset detail is
// an inspector for one asset, not a result category, so it belongs to no group.

export type ResultsTab =
  | 'summary' | 'overview' | 'capex' | 'dispatch' | 'loadflow' | 'prices' | 'emissions'
  | 'economics' | 'curtailment' | 'lostload' | 'adequacy' | 'storage'
  | 'asset' | 'fmea' | 'investment' | 'flows' | 'loadshape'

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
    tabs: ['dispatch', 'flows', 'loadshape', 'storage', 'curtailment', 'emissions'] },
]

export function groupOf(tab: ResultsTab): ResultsGroup | null {
  return RESULT_GROUPS.find(g => g.tabs.includes(tab))?.id ?? null
}

// ── Card groups (W10 R1, .scratch/project-workflow/card-workspaces.md §3, §4, §8) ──
// The Results panel's groups are the work cards of `flow_scope.CARDS`, plus the
// Summary landing. R1 mounts only today's tabs: each is filed under the card and
// card tab its `ResultView` names (`services/result_views.py`). Card and tab ids
// below must match that registry; Asset Detail belongs to none (a drawer).

import type { ScopeCardId } from '../../api/flow'

export type CardGroupId = 'summary' | ScopeCardId

export interface CardResultTab {
  id: string
  label: string
  /** Today's tabs this card tab holds; more than one shows a sub-view row. */
  views: readonly ResultsTab[]
}

export interface CardGroup {
  id: CardGroupId
  label: string
  question: string
  tabs: readonly CardResultTab[]
}

export const CARD_GROUPS: readonly CardGroup[] = [
  { id: 'summary', label: 'Summary', question: 'The solve at a glance',
    tabs: [{ id: 'summary', label: 'Summary', views: ['summary'] }] },
  { id: 'capacity', label: 'Capacity expansion', question: 'What to build, and how it runs',
    tabs: [
      { id: 'build', label: 'Build', views: ['capex', 'overview'] },
      { id: 'operation', label: 'Operation', views: ['dispatch', 'flows', 'loadshape', 'storage', 'emissions', 'lostload'] },
      { id: 'curtailment', label: 'Curtailment', views: ['curtailment'] },
      { id: 'costs_prices', label: 'Costs & prices', views: ['prices', 'economics'] },
    ] },
  { id: 'grid', label: 'AC power flow & grid', question: 'Whether the network and the connection carry it',
    tabs: [{ id: 'load_flow', label: 'Load flow', views: ['loadflow'] }] },
  { id: 'control', label: 'Control', question: 'How the plant is controlled', tabs: [] },
  { id: 'reliability', label: 'Reliability', question: 'Whether the critical load stays on',
    tabs: [
      { id: 'adequacy', label: 'Adequacy', views: ['adequacy'] },
      { id: 'stress_fmea', label: 'Stress & FMEA', views: ['fmea'] },
    ] },
  { id: 'commercial', label: 'Commercial & finance', question: 'What it costs and earns',
    tabs: [{ id: 'investment_case', label: 'Investment case', views: ['investment'] }] },
  { id: 'handoff', label: 'Hand-off', question: 'What leaves the tool', tabs: [] },
]

/** The group and card tab a Results tab sits under; null for Asset Detail. */
export function cardPlaceOf(tab: ResultsTab): { group: CardGroupId; cardTab: string } | null {
  for (const g of CARD_GROUPS) {
    const t = g.tabs.find(x => x.views.includes(tab))
    if (t) return { group: g.id, cardTab: t.id }
  }
  return null
}
