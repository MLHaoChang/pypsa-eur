// Project-scoped React Query key factory.
//
// React Query keys for per-project data were historically FLAT (`['buses']`,
// `['meta']`, …) with no project dimension, so multiple resident projects
// would collide in cache. `nk` ("network key") prefixes the root with the
// current project id so each project gets its own cache namespace:
//
//   nk('proj-a', 'buses')           → ['buses', 'proj-a']
//   nk('proj-a', 'load_profiles', x) → ['load_profiles', 'proj-a', x]
//   nk(null, 'buses')               → ['buses', null]   (valid "no-project" ns)
//
// While the app is single-active (one `currentProject`), `[root, currentProject]`
// behaves identically to the old `[root]` — one namespace. The multi-project
// benefit (instant switch keeping each project's cache) lands later.
//
// IMPORTANT — getQueryData ⇄ useQuery parity: a read-before-PUT
// `qc.getQueryData(nk(projectId, root))` MUST pass the SAME projectId the
// matching `useQuery({ queryKey: nk(currentProject, root) })` used to write
// that cache entry, or the read silently returns undefined (which wipes
// fields on the subsequent PUT). In a render/hook read `currentProject` via
// the reactive selector; in a non-React callback read it via
// `useUIStore.getState().currentProject`.
export function nk(projectId: string | null, root: string, ...rest: unknown[]): unknown[] {
  return [root, projectId, ...rest]
}
