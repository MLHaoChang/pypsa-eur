// GET /results/operational_gap (DC grid readiness 07, S3): the operational
// (rolling) run against a full-horizon dispatch on the same sizes,
// `backend/services/solver/operational_gap.py`. The route never solves.
//
// `metrics` is null unless the flag is `fresh`; a metric side the network
// cannot give is null with its reason, never 0. 409 `solver_in_flight` while
// a solve runs (raised quietly here).
import client from './client'

/** The route's five flags (`operational_gap.FRESH` … `REFERENCE_NOT_SOLVED`);
 *  `tests/test_operational_gap_words.py` fails when they drift. */
export const OP_GAP_FLAGS = [
  'fresh', 'missing', 'stale', 'operational_window_failed', 'reference_not_solved',
] as const
export type OperationalGapFlag = typeof OP_GAP_FLAGS[number]

export interface OperationalGapRow {
  definition: string
  operational: number | null
  reference: number | null
  /** operational − reference; null when either side is null. */
  gap: number | null
  /** 100 × gap / |reference|; null when either side is null or the reference is 0. */
  gap_pct: number | null
  operational_reason?: string
  reference_reason?: string
}

export interface OperationalGapPayload {
  label: string
  flag: OperationalGapFlag
  opex_basis: string
  excluded_terms: string[]
  claim_limit: string
  gap_definition: string
  windows_definition: string
  definitions: Record<string, string>
  metrics: Record<string, OperationalGapRow> | null
  windows: { total: number; solved: number; failed: number; failed_index: number | null } | null
  reference: { status: string; condition: string } | null
  /** Present unless the flag is missing or stale. */
  horizon?: number | null
  overlap?: number | null
}

export const operationalGapApi = {
  get: () =>
    client.get<OperationalGapPayload>('/results/operational_gap', { skipErrorToast: true })
      .then(r => r.data),
}
