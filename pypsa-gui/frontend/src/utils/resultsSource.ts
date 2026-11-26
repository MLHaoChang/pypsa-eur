export interface ResultsSourceBinding {
  version: 1
  project_id: string
  project_name?: string | null
  scope: 'current' | 'historical'
  data_sha256: string
  input_sha256?: string
  source?: 'lopf' | 'ac_pf'
  /** null: the route's default window (A5.2 S3 views record the call they made). */
  window_from?: number | null
  window_to?: number | null
  /** A5.2 S3: the period and week a bound view was read for (null: the route's default). */
  period?: number | null
  week?: number | null
  /** A5.2 S3: a multi-payload view's hash of the part the chat read names. */
  read_sha256?: string
  run_id?: string
  section?: string | null
  baseline_run_id?: string | null
  /** A results tab's bound view (A5.2): which tab's payload `data_sha256` hashes. */
  view?: string
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
          'source', 'window_from', 'window_to', 'run_id', 'section', 'baseline_run_id', 'view',
          'period', 'week', 'read_sha256']
        return Object.fromEntries(keys.filter(k => Object.prototype.hasOwnProperty.call(value, k)
          && (value[k as keyof ResultsSourceBinding] === null || typeof value[k as keyof ResultsSourceBinding] === 'string'
            || typeof value[k as keyof ResultsSourceBinding] === 'number' && Number.isFinite(value[k as keyof ResultsSourceBinding])))
          .map(k => [k, value[k as keyof ResultsSourceBinding]])) as unknown as ResultsSourceBinding
      }
    } catch { /* A malformed or unavailable view remains unacknowledged. */ }
  }
  return null
}
