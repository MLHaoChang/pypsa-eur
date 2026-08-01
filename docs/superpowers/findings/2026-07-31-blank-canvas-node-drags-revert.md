# Blank-canvas node drags revert — diagnosis handover

**Date:** 2026-07-31
**Reported by:** the user — "dragging the map in the blank canvas does not save its topology";
on follow-up, confirmed as **node positions reverting**, not the camera.
**Status:** diagnosed, NOT fixed. Handed to whoever owns `TopologyCanvas.tsx` /
`topologyLayoutStore.ts` — that file had three sessions in it on 2026-07-31 and
a layout-persistence refactor landed the same day (`02c95540`, `5ec412e4`).

## What is already correct

Do not re-derive these; they were checked.

- `onNodeDragStop` calls `scheduleSave()` (`TopologyCanvas.tsx:2618`).
- `useLayoutPersistence` builds the payload from the commit that carries the
  change, debounces 300 ms, writes the module cache synchronously, then calls
  `persistLayoutFor`.
- `persistLayoutFor` **does** write to the server: `projectsApi.putLayout` →
  `PUT /api/projects/{name}/layout`. It is not localStorage-only.
- `persistLayoutOnUnload` covers F5 / tab close with `fetch(keepalive)`.
- `layoutMemCache` covers a TopologyCanvas unmount (blank ↔ satellite switch).
- `topologyLayoutStore.test.tsx` passes 8/8.

## The two facts that make a revert possible

**1. `PUT /layout` 404s until the project directory exists on disk.** Measured
against an authenticated client on an isolated backend:

```
PUT /layout BEFORE the project is saved  -> 404 {"detail":"Project not found"}
POST /projects/{name} (save)             -> 200
PUT /layout AFTER save                   -> 200 {"saved":"ProbeProj"}
GET /layout                              -> the new positions, intact
```

This is why `AppHeader.tsx:354`, `Sidebar.tsx:886` and `projectActions.ts:466`
all order the layout PUT *after* the network save. A drag-triggered save has no
such ordering — it fires whenever the user drags.

**2. On load, the server wins unconditionally over anything newer held
locally.** `TopologyCanvas.tsx:2006`:

```ts
const resolved = ps
  ?? layoutMemCache.get(layoutCacheKey(currentProject))
  ?? loadDiagramState(currentProject)
  ?? null
```

`persistLayoutFor`'s `.catch()` falls back to `saveDiagramState` (localStorage)
when the server write fails — but that fallback is **third in this chain**. So
any failed PUT leaves a newer local layout that the next load discards in
favour of the older `layout.json`. The failure is silent at both ends: the
`.catch()` swallows the error, and the load treats "server returned something"
as authoritative regardless of age.

`PersistedState` carries `savedAt`, so the information needed to prefer the
newer copy is already on both sides and is simply not consulted.

## What has NOT been reproduced

The user's exact sequence. A previously-saved project, dragged and reloaded,
round-trips correctly in isolation. So a trigger is still missing — the PUT
must be failing for a reason not yet identified. Candidates, in the order worth
testing:

1. **The project had never been saved when the drag happened** (404 above). Ask
   the user whether the project was newly created or opened from disk. If new,
   this is the whole story and the fix is ordering, not merge logic.
2. **A write-gate refusal.** `main.py`'s middleware 409s writes while a solve is
   running, and there is a read-only / lock path (`readOnly`, `lockHolderEmail`
   in `uiStore`). Either would make the PUT fail and hand the revert its
   opportunity.
3. **A CSRF 403** on the unload path specifically — `persistLayoutOnUnload`
   builds headers with `rawFetchHeaders('PUT')` rather than going through the
   axios interceptor.

## The experiment that settles it

Instrument `persistLayoutFor`'s `.catch()` to log status and body — today it
discards both. Then drag a node in the app and read the log. One line of
logging converts this from three candidates to one fact.

## Where a fix belongs (not applied)

- **Ordering:** a drag-triggered layout write against a project with no
  directory on disk should either create it or defer, rather than 404 into a
  silent localStorage fallback.
- **Merge, not precedence:** at `TopologyCanvas.tsx:2006`, compare `savedAt`
  and take the newer of server vs. local instead of `ps ??`. Both sides already
  carry the timestamp.
- **Surface the failure:** the `.catch()` should tell the user their layout did
  not reach the server. A layout that silently lives only in localStorage looks
  saved until the next load.

## Separately, and definitely real

**The camera is not persisted, on either canvas.** `PersistedState` is
`{version, savedAt, nodes, edges}` — no viewport. The blank canvas has no
`onMoveEnd` and no `defaultViewport`, and `fitView({padding: 0.25})` runs once
when nodes first appear (`TopologyCanvas.tsx:2414`). The map canvas is the same
shape via `FitToNetwork`'s `fittedRef`. So pan/zoom is discarded on every
reload, project switch and canvas-view toggle. This is a separate gap from the
reverting nodes and was the first reading of the report; it is worth fixing on
its own, but it is not what the user hit.
