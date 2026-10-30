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
