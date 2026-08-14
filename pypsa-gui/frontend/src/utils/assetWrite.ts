/**
 * The Asset-write module (see CONTEXT.md → "Asset write").
 *
 * Owns the idiom every asset update must follow — and, first, the piece whose
 * absence was a live defect: cache invalidation after chat tool mutations.
 * ChatPanel invalidated only meta/simulationStatus/snapshots on project
 * rebind; none of the 64 mutating chat tools invalidated any component query,
 * so a chat edit followed by any manual edit of the same asset spread the
 * STALE cached row into the PUT and the backend's remove+add cycle silently
 * reverted the agent's work.
 *
 * Grilled 2026-08-14 (docs/superpowers/plans/2026-08-14-asset-write-chokepoint.md):
 *  1. this table ships with the defect fix and the chokepoint grows from it;
 *  2. invalidation is tier-keyed and blanket — no per-tool mapping to rot;
 *  3. cache miss is fetch-then-spread (Task 3);
 *  4. updates only.
 */
import type { QueryClient } from '@tanstack/react-query'

import { nk } from './queryKeys'

/**
 * Every query-key root whose rows a mutating chat tool (or any asset write)
 * can change. One entry per component class in `networkApi`'s update surface,
 * plus `meta` (component counts render in the status bar).
 *
 * NOT here: `snapshots` / `simulationStatus` — project-lifecycle families with
 * their own invalidation sites (ChatPanel's project_rebound handler).
 */
export const COMPONENT_QUERY_ROOTS = [
  'buses',
  'carriers',
  'lines',
  'links',
  'generators',
  'storage_units',
  'stores',
  'loads',
  'transformers',
  'meta',
] as const

export type ComponentRoot = (typeof COMPONENT_QUERY_ROOTS)[number]

/**
 * Does a chat tool with this safety tier mutate network state?
 *
 * `read` is the only non-mutating tier in the backend's vocabulary
 * (read/write/destructive/execution/execution_long_running — the `Safety:`
 * markers in chat_tools_schema.py, maintained because they gate confirmation
 * cards). Anything else — including an ABSENT tier, e.g. a `tool_result`
 * whose `tool_request` frame was never seen — counts as mutating, because
 * the failure modes are asymmetric: a spurious refetch costs a GET, a missed
 * invalidation silently reverts the agent's work.
 */
export function isMutatingTier(tier: string | undefined | null): boolean {
  return tier !== 'read'
}

/**
 * Blanket-invalidate every component-level query family for `project`.
 * Deliberately NOT surgical: a per-tool → per-class mapping would be a new
 * 64-row drift surface of exactly the kind that caused the defect this
 * closes. Chat mutations are rare; React Query dedupes refetches for
 * mounted queries.
 */
export function invalidateAssetQueries(qc: QueryClient, project: string | null): void {
  for (const root of COMPONENT_QUERY_ROOTS) {
    qc.invalidateQueries({ queryKey: nk(project, root) })
  }
}
