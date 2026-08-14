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

import { networkApi } from '../api/network'
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

/** An asset row as the chokepoint sees it: a name plus whatever the class carries. */
type AssetRow = { name: string } & Record<string, unknown>

/** One row of the per-class dispatch table: how to list and how to PUT. */
type Endpoints = {
  get: () => Promise<AssetRow[]>
  put: (name: string, body: AssetRow) => Promise<unknown>
}

/**
 * Widen a class's typed api pair to the chokepoint's row-agnostic shape.
 * The casts are safe because both sides are the SAME runtime functions —
 * per-class typing is enforced where it means something: at the call site
 * (a typed `Partial<Generator>` patch) and inside networkApi itself. The
 * chokepoint's job is the idiom, not the field vocabulary.
 */
function ep<G extends { name: string }, P>(
  get: () => Promise<G[]>,
  put: (name: string, body: P) => Promise<unknown>,
): Endpoints {
  return {
    get: get as unknown as Endpoints['get'],
    put: put as unknown as Endpoints['put'],
  }
}

/**
 * `meta` is a roll-up, not a writable class, so the update surface is the
 * table below rather than COMPONENT_QUERY_ROOTS itself.
 */
const ENDPOINTS = {
  buses:         ep(networkApi.getBuses,        networkApi.updateBus),
  carriers:      ep(networkApi.getCarriers,     networkApi.updateCarrier),
  lines:         ep(networkApi.getLines,        networkApi.updateLine),
  links:         ep(networkApi.getLinks,        networkApi.updateLink),
  generators:    ep(networkApi.getGenerators,   networkApi.updateGenerator),
  storage_units: ep(networkApi.getStorageUnits, networkApi.updateStorageUnit),
  stores:        ep(networkApi.getStores,       networkApi.updateStore),
  loads:         ep(networkApi.getLoads,        networkApi.updateLoad),
  transformers:  ep(networkApi.getTransformers, networkApi.updateTransformer),
}

export type WritableRoot = keyof typeof ENDPOINTS

/**
 * The Asset-write chokepoint: fetch → spread → PUT → invalidate.
 *
 * The backend's `_update_component` is remove+add — any field the PUT omits
 * resets to its Pydantic default (the B1/B2 corruption class). Every caller
 * therefore hands over a PATCH of what changed, and this function owns the
 * spread of the full current row underneath it.
 *
 * Cache miss is fetch-then-spread (ruling 3): `ensureQueryData` returns the
 * cached list or fetches it, so the miss case stops existing rather than
 * being handled — no throw-on-cold-cache, no closure fallback, and the
 * bare-fields PUT is unrepresentable through this path. An error here means
 * the asset genuinely does not exist in the current network.
 */
export async function updateAsset<T extends { name: string } = AssetRow>(
  qc: QueryClient,
  project: string | null,
  cls: WritableRoot,
  name: string,
  // Either the patch itself, or a BUILDER given the current row — for
  // mappings whose fallbacks need `current` (nf(form, 'p_nom',
  // current.p_nom) in the PropertiesPanel cards). The builder keeps that
  // per-form knowledge at the call site; the chokepoint still owns fetch,
  // spread, PUT and invalidation either way.
  //
  // NoInfer: without it TS infers T from the patch literal itself ({p_nom:1}
  // fails the name-constraint and collapses T to {name: string}, rejecting
  // every real field). T comes only from explicit annotation —
  // updateAsset<Generator>(...) for field-checked call sites — and defaults
  // to the open AssetRow for untyped ones.
  patch: Partial<NoInfer<T>> | ((current: NoInfer<T>) => Partial<NoInfer<T>>),
): Promise<void> {
  const e = ENDPOINTS[cls]
  const rows = await qc.ensureQueryData({
    queryKey: nk(project, cls),
    queryFn: e.get,
  })
  const current = rows.find((r) => r.name === name)
  if (!current) {
    throw new Error(`${cls}/${name} not found in the current network`)
  }
  // The rows really are T (getGenerators returns Generator[]); the table
  // widened them to AssetRow at its seam, so narrow back at this one.
  const resolved = typeof patch === 'function' ? patch(current as unknown as T) : patch
  await e.put(name, { ...current, ...resolved })
  invalidateAssetQueries(qc, project)
}
