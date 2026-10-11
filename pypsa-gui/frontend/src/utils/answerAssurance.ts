/** Mechanical receipt verification; this is not a scientific confidence score. */
export interface AnswerReceipt {
  policy_version: string
  content_sha256: string
  evidence_ids: string[]
  checked_block_ids: string[]
  limitations: string[]
  reviewer_status: string
  request_complete?: boolean
  request_scope?: { profile: string; entity_id?: string; project_id?: string; start?: string; end?: string; retain?: boolean }
  result_slots?: { metric: string; disposition: 'answered' | 'unavailable'; reason?: string; valid_snapshots?: number; requested_snapshots?: number }[]
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
    || !['passed', 'limited'].includes(String(value.verdict)) || !receipt
    || typeof receipt.content_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(receipt.content_sha256)
    || receipt.policy_version !== '1'
    || !REVIEW_STATUSES.has(String(receipt.reviewer_status))
    || (receipt.request_complete !== undefined && typeof receipt.request_complete !== 'boolean')
    || (scope !== undefined && (!scope || typeof scope !== 'object' || Array.isArray(scope)
      || typeof scope.profile !== 'string' || scope.profile.length > 64
      || ['entity_id', 'project_id', 'start', 'end'].some(k => {
        const v = scope[k as keyof typeof scope]
        return v !== undefined && (typeof v !== 'string' || v.length > 128)
      }) || (scope.retain !== undefined && typeof scope.retain !== 'boolean')))
    || (receipt.result_slots !== undefined && (!Array.isArray(receipt.result_slots) || receipt.result_slots.length > 20
      || receipt.result_slots.some(s => !s || typeof s.metric !== 'string' || s.metric.length > 64
        || !['answered', 'unavailable'].includes(s.disposition)
        || (s.reason !== undefined && (typeof s.reason !== 'string' || s.reason.length > 4096))
        || [s.valid_snapshots, s.requested_snapshots].some(v => v !== undefined
          && (!Number.isInteger(v) || v < 0 || v > 512)))))
    || !['evidence_ids', 'checked_block_ids', 'limitations'].every(key => {
      const values = receipt[key as keyof AnswerReceipt]
      return Array.isArray(values) && values.length <= 20
        && values.every(v => typeof v === 'string' && v.length <= 4096)
    })) return null
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
