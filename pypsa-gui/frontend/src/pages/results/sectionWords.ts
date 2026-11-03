/** The Results tab each retained section (`services/retained_results.py`) belongs to. */
export const SECTION_TAB: Record<string, string> = {
  loadflow: 'loadflow', results_summary: 'summary', results_capacity: 'capex',
  results_dispatch: 'dispatch', results_prices: 'prices', results_emissions: 'emissions',
}

/** A retained section by its Results tab's own label: "results_capacity" → "Capacity expansion". */
export function sectionWords(section: string, tabLabel: (id: string) => string | undefined): string {
  const id = SECTION_TAB[section]
  const label = id ? tabLabel(id) : undefined
  if (label) return label
  const w = section.replace(/^results_/, '').replace(/_/g, ' ').trim()
  return w ? w.charAt(0).toUpperCase() + w.slice(1) : section
}

/** Each not_kept reason (`backend/services/not_kept.py`) in words: the clause after
 *  "This run kept no …:", and the short form a section list shows. */
export const NOT_KEPT_WORDS: Record<string, { why: string; short: string }> = {
  multi_period_not_digested: { why: 'multi-period runs keep no dispatch or prices', short: 'multi-period run' },
  no_prices: { why: 'the solve produced no prices', short: 'no prices' },
  over_section_cap: { why: 'too large to keep', short: 'too large' },
}

/** One sentence for a section kept as a not_kept stub, shared by the past-run view and
 *  the Export picker's refusal. */
export function notKeptSentence(section: string, reason: string | undefined,
  tabLabel: (id: string) => string | undefined): string {
  const why = (reason && NOT_KEPT_WORDS[reason]?.why) || 'it could not be kept'
  return `This run kept no ${sectionWords(section, tabLabel)}: ${why}. Current results are not shown in their place.`
}

/** "Prices (not kept: multi-period run)" in a list of a run's sections. */
export function notKeptShort(reason: string | undefined): string {
  return (reason && NOT_KEPT_WORDS[reason]?.short) || 'could not be kept'
}
