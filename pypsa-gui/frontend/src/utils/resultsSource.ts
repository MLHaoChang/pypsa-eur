export interface ResultsSourceBinding {
  version: 1
  project_id: string
  project_name?: string | null
  scope: 'current' | 'historical'
  data_sha256: string
  input_sha256?: string
  source?: 'lopf' | 'ac_pf'
  window_from?: number
  window_to?: number
  run_id?: string
  section?: string | null
  baseline_run_id?: string | null
}

export function sameResultsSource(a: ResultsSourceBinding, b: ResultsSourceBinding): boolean {
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()
  return keys.every(k => a[k as keyof ResultsSourceBinding] === b[k as keyof ResultsSourceBinding])
}

/** Read the adapter's fetched-and-rendered evidence marker, never a tab mount. */
export function displayedResultsSource(tab: string, root: Document | HTMLElement = document): ResultsSourceBinding | null {
  for (const element of root.querySelectorAll<HTMLElement>('[data-result-binding]')) {
    if (element.dataset.resultTab !== tab || element.closest('[hidden], [aria-hidden="true"]')) continue
    let visible = true
    for (let p: HTMLElement | null = element; p; p = p.parentElement) {
      const style = getComputedStyle(p)
      if (style.display === 'none' || style.visibility === 'hidden') visible = false
    }
    if (!visible) continue
    try {
      const value = JSON.parse(element.dataset.resultBinding ?? '') as ResultsSourceBinding
      if (value.version === 1 && typeof value.project_id === 'string'
        && /^(?:current|historical)$/.test(value.scope) && /^[a-f0-9]{64}$/.test(value.data_sha256)) {
        const keys = ['version', 'project_id', 'project_name', 'scope', 'data_sha256', 'input_sha256',
          'source', 'window_from', 'window_to', 'run_id', 'section', 'baseline_run_id']
        return Object.fromEntries(keys.filter(k => Object.prototype.hasOwnProperty.call(value, k)
          && (value[k as keyof ResultsSourceBinding] === null || typeof value[k as keyof ResultsSourceBinding] === 'string'
            || typeof value[k as keyof ResultsSourceBinding] === 'number' && Number.isFinite(value[k as keyof ResultsSourceBinding])))
          .map(k => [k, value[k as keyof ResultsSourceBinding]])) as unknown as ResultsSourceBinding
      }
    } catch { /* A malformed or unavailable view remains unacknowledged. */ }
  }
  return null
}
