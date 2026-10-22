/** Mechanical receipt verification; this is not a scientific confidence score. */
export interface AnswerReceipt {
  policy_version: string
  content_sha256: string
  evidence_ids: string[]
  checked_block_ids: string[]
  limitations: string[]
  reviewer_status: string
  request_complete?: boolean
  request_scope?: { profile: string; entity_id?: string; project_id?: string; start?: string; end?: string; retain?: boolean;
    cost_basis?: { currency: 'EUR'; price_year: number; annual_fixed_cost_fraction: number } }
  result_slots?: { metric: string; disposition: 'answered' | 'unavailable' | 'needs_clarification'; reason?: string; valid_snapshots?: number; requested_snapshots?: number }[]
  chart?: CheckedChart
  navigation?: NavigationEntry[]
}

/** A navigation outcome; for Load flow in a checked loading turn, whether the tab
 * shows the solve and window that were checked (S3b). */
export interface NavigationEntry {
  action_id: string
  outcome: string
  shows?: 'checked_evidence' | 'different_view' | 'not_confirmed'
  reason?: 'window_differs' | 'source_not_lopf' | 'inputs_changed' | 'fingerprint_basis_unknown' | 'project_differs'
  window?: { start: string; end: string }
}

const SHOWS = new Set(['checked_evidence', 'different_view', 'not_confirmed'])
const VIEW_REASONS = new Set(['window_differs', 'source_not_lopf', 'inputs_changed', 'fingerprint_basis_unknown',
  'project_differs'])

function validNavigation(entries: unknown): boolean {
  return Array.isArray(entries) && entries.length <= 20 && entries.every(e => {
    if (!e || typeof e !== 'object' || Array.isArray(e)) return false
    const n = e as Partial<NavigationEntry>
    if (typeof n.action_id !== 'string' || n.action_id.length > 128 || typeof n.outcome !== 'string'
      || n.outcome.length > 64) return false
    if (n.shows === undefined) return n.reason === undefined && n.window === undefined
    if (!SHOWS.has(n.shows)) return false
    if (n.shows === 'different_view') return typeof n.reason === 'string' && VIEW_REASONS.has(n.reason) && n.window === undefined
    if (n.shows === 'checked_evidence') return n.reason === undefined && !!n.window
      && typeof n.window.start === 'string' && n.window.start.length <= 64
      && typeof n.window.end === 'string' && n.window.end.length <= 64
    return n.reason === undefined && n.window === undefined
  })
}

/** A checked chart (S10): drawn from the answer's own evidence, listed by series. */
export interface CheckedChart {
  status: 'drawn' | 'not_drawn'
  reason?: string
  title?: string
  file_id?: string
  filename?: string
  download_url?: string
  png_sha256?: string
  evidence_id?: string
  window?: { start: string | null; end: string | null; snapshots: number }
  series: { series: string; disposition: 'drawn' | 'not_drawn'; unit?: string; reason?: string; title?: string }[]
  caption?: string[]
}

const CHART_BLOB = /^\/api\/projects\/[^/?#]+\/uploads\/[0-9a-f]{16}\/blob$/
const short = (v: unknown, max: number) => v === undefined || (typeof v === 'string' && v.length <= max)

/** The chart block's shape; a drawn chart must name its file, evidence and window. */
export function validChart(chart: unknown): chart is CheckedChart {
  if (!chart || typeof chart !== 'object' || Array.isArray(chart)) return false
  const c = chart as Partial<CheckedChart>
  if (!['drawn', 'not_drawn'].includes(c.status as string) || !Array.isArray(c.series) || c.series.length < 1
    || c.series.length > 8 || !short(c.reason, 64) || !short(c.title, 200)
    || c.series.some(s => !s || typeof s.series !== 'string' || s.series.length > 64
      || !['drawn', 'not_drawn'].includes(s.disposition) || !short(s.unit, 32) || !short(s.reason, 64)
      || !short(s.title, 200) || (s.disposition === 'not_drawn' && typeof s.title !== 'string'))
    || (c.caption !== undefined && (!Array.isArray(c.caption) || c.caption.length > 12
      || c.caption.some(line => typeof line !== 'string' || line.length > 400)))) return false
  if (c.status === 'not_drawn') return c.series.every(s => s.disposition === 'not_drawn')
  const w = c.window
  return typeof c.download_url === 'string' && CHART_BLOB.test(c.download_url)
    && typeof c.file_id === 'string' && /^[0-9a-f]{16}$/.test(c.file_id) && c.download_url.includes(`/${c.file_id}/`)
    && typeof c.png_sha256 === 'string' && /^[0-9a-f]{64}$/.test(c.png_sha256)
    && typeof c.evidence_id === 'string' && c.evidence_id.length <= 64 && short(c.filename, 180)
    && !!w && typeof w === 'object' && short(w.start ?? undefined, 64) && short(w.end ?? undefined, 64)
    && Number.isInteger(w.snapshots) && w.snapshots >= 1 && w.snapshots <= 1200
    && c.series.some(s => s.disposition === 'drawn')
}

const REVIEW_STATUSES = new Set(['not_required', 'passed', 'failed', 'unavailable',
  'withheld', 'withheld_budget', 'withheld_disabled', 'withheld_invalid_draft',
  'withheld_limit', 'withheld_tool_attempt', 'withheld_usage', 'withheld_schema',
  'withheld_incomplete', 'withheld_failure', 'withheld_timeout'])

export interface ApprovedAnswerFrame {
  version: 1
  answer_id: string
  turn_id: string
  verdict: 'passed' | 'limited'
  receipt: AnswerReceipt
}

export function parseApproval(value: Record<string, unknown>): ApprovedAnswerFrame | null {
  const receipt = value.receipt as Partial<AnswerReceipt> | null
  const scope = receipt?.request_scope
  if (value.version !== 1 || typeof value.answer_id !== 'string' || !value.answer_id
    || value.answer_id.length > 128
    || typeof value.turn_id !== 'string' || !value.turn_id
    || value.turn_id.length > 128
    || typeof value.verdict !== 'string' || !['passed', 'limited'].includes(value.verdict) || !receipt
    || typeof receipt.content_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(receipt.content_sha256)
    || receipt.policy_version !== '1'
    || typeof receipt.reviewer_status !== 'string' || !REVIEW_STATUSES.has(receipt.reviewer_status)
    || (receipt.request_complete !== undefined && typeof receipt.request_complete !== 'boolean')
    || (scope !== undefined && (!scope || typeof scope !== 'object' || Array.isArray(scope)
      || typeof scope.profile !== 'string' || scope.profile.length > 64
      || ['entity_id', 'project_id', 'start', 'end'].some(k => {
        const v = scope[k as keyof typeof scope]
        return v !== undefined && (typeof v !== 'string' || v.length > 128)
      }) || (scope.retain !== undefined && typeof scope.retain !== 'boolean')
      || (scope.cost_basis !== undefined && (!scope.cost_basis || typeof scope.cost_basis !== 'object'
        || Array.isArray(scope.cost_basis) || scope.cost_basis.currency !== 'EUR'
        || !Number.isInteger(scope.cost_basis.price_year) || scope.cost_basis.price_year < 1900 || scope.cost_basis.price_year > 2200
        || typeof scope.cost_basis.annual_fixed_cost_fraction !== 'number'
        || !Number.isFinite(scope.cost_basis.annual_fixed_cost_fraction)
        || scope.cost_basis.annual_fixed_cost_fraction < 0 || scope.cost_basis.annual_fixed_cost_fraction > 1
        || Object.keys(scope.cost_basis).some(k => !['currency', 'price_year', 'annual_fixed_cost_fraction'].includes(k))))))
    || (receipt.result_slots !== undefined && (!Array.isArray(receipt.result_slots) || receipt.result_slots.length > 20
      || receipt.result_slots.some(s => !s || typeof s.metric !== 'string' || s.metric.length > 64
        || (typeof s.disposition !== 'string' || !['answered', 'unavailable', 'needs_clarification'].includes(s.disposition))
        || (s.reason !== undefined && (typeof s.reason !== 'string' || s.reason.length > 4096))
        || [s.valid_snapshots, s.requested_snapshots].some(v => v !== undefined
          && (!Number.isInteger(v) || v < 0 || v > 512)))))
    || (receipt.chart !== undefined && !validChart(receipt.chart))
    || (receipt.navigation !== undefined && !validNavigation(receipt.navigation))
    || !['evidence_ids', 'checked_block_ids', 'limitations'].every(key => {
      const values = receipt[key as keyof AnswerReceipt]
      return Array.isArray(values) && values.length <= 20
        && values.every(v => typeof v === 'string' && v.length <= 4096)
    })) return null
  const clarifying = receipt.result_slots?.some(s => s.disposition === 'needs_clarification')
  if (clarifying && (value.verdict !== 'limited' || receipt.request_complete !== false
    || receipt.checked_block_ids!.length > 0 || receipt.evidence_ids!.length > 0
    || receipt.result_slots!.some(s => s.disposition !== 'needs_clarification'))) return null
  return value as unknown as ApprovedAnswerFrame
}

export async function verifyApprovedText(text: string, expected: string): Promise<boolean> {
  const bytes = new TextEncoder().encode(text)
  if (!text.trim() || bytes.byteLength > 64 * 1024 || !globalThis.crypto?.subtle) return false
  try {
    const hash = await crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('') === expected
  } catch {
    return false
  }
}
