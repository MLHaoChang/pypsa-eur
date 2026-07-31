import { create } from 'zustand'
import type { RescalePreview } from '../utils/rescale'

// App-wide queue of impedance-rescale previews awaiting a user decision
// (D-B4). Lines store r/x/b absolute but display them per-km, so a length
// change either silently changes the per-km value or — if the backend's
// preview is applied — changes x (and DC OPF splits flows inversely
// proportional to x, so results move). Either way it needs a decision
// somewhere; this store is where that decision waits.
//
// Lifted out of MapCanvasInner's local `useState` (2026-07-31 review,
// Finding 1): FOUR other write paths besides MapCanvas's own drag/recalc
// handlers produce a `rescale` preview from the backend — PropertiesPanel's
// Bus form, TopologyCanvas's BusEditor, and the two chat tools
// (`update_component`/`recalculate_line_lengths`, backend-only, see
// utils/rescaleActions.ts's module comment for that half). Before the lift,
// only MapCanvas fed its own previews into state; every other caller's
// preview was computed by the backend and then discarded by the response
// handler. Keeping this as a dedicated store (mirroring `chatStore.ts` /
// `simulationStore.ts` rather than growing the already-large `uiStore.ts`)
// keeps the concern isolated and easy to find.
//
// State only — no API calls, no toasts, no QueryClient. The ingest/apply
// orchestration lives in `utils/rescaleActions.ts` (module-level functions
// that take a QueryClient explicitly, same pattern as `utils/projectActions.ts`),
// so this file stays trivially testable and has no dependency on React Query.
interface RescaleStore {
  // Previews awaiting a decision. RescaleDialogHost re-partitions this on
  // every render via the same `partitionRescale` that routed them here, into
  // "ask" (skipped_reason === null) and "blocked" (skipped_reason !== null) —
  // never re-derives the 5% threshold itself.
  pendingRescale: RescalePreview[]
  setPendingRescale: (
    updater: RescalePreview[] | ((prev: RescalePreview[]) => RescalePreview[])
  ) => void
  // True while MapCanvas's click-to-place mode is running. RescaleDialogHost
  // reads this to withhold the modal while placement is active — a Dialog
  // stealing focus mid-click would break click-to-place (B5) — without
  // needing to import MapCanvas or know it exists. MapCanvas is the only
  // writer (see its placing-sync effect in MapCanvas.tsx); `ingestRescale`
  // still queues into `pendingRescale` during placement, so nothing is lost,
  // it just doesn't surface until placement ends (placementActive → false)
  // one way or another (last bus placed, Escape, "Done", or unmount).
  placementActive: boolean
  setPlacementActive: (active: boolean) => void
}

export const useRescaleStore = create<RescaleStore>((set) => ({
  pendingRescale: [],
  setPendingRescale: (updater) => set(s => ({
    pendingRescale: typeof updater === 'function'
      ? (updater as (prev: RescalePreview[]) => RescalePreview[])(s.pendingRescale)
      : updater,
  })),
  placementActive: false,
  setPlacementActive: (active) => set({ placementActive: active }),
}))
