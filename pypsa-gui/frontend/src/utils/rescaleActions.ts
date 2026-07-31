// Ingest/apply orchestration for impedance-rescale previews (D-B4). Paired
// with `store/rescaleStore.ts` (state) and `components/RescaleDialogHost.tsx`
// (the single app-wide dialog instance) — see rescaleStore's module comment
// for why this was lifted out of MapCanvasInner.
//
// Module-level functions taking a QueryClient explicitly, not store actions
// that close over one — same pattern as `utils/projectActions.ts`
// (`invalidateNetworkQueries(qc, projectId)`), which keeps the store itself
// free of a React Query dependency.
//
// Chat-driven bus moves and `recalculate_line_lengths` calls (backend tools
// in services/chat_tools.py) are OUT OF SCOPE here by design — see the task
// report for what happens to their `rescale` field today. Nothing in this
// file is reachable from the chat path.
import type { QueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { networkApi } from '../api/network'
import { nk } from './queryKeys'
import { partitionRescale, type RescalePreview } from './rescale'
import { useUIStore } from '../store/uiStore'
import { useRescaleStore } from '../store/rescaleStore'

// The only write path (B1): explicit, and only for previews the caller
// already decided to apply (the auto-applied immaterial ones, or the user's
// "Update" choice on the dialog). Toasts and rethrows on failure — one
// message either call site can rely on, rather than duplicating it — so
// BOTH callers (the silent auto path in `ingestRescale`, and
// RescaleDialogHost's `onAccept`) can still tell the write didn't happen and
// react.
export async function applyRescale(qc: QueryClient, previews: RescalePreview[]): Promise<void> {
  if (previews.length === 0) return
  try {
    await networkApi.rescaleImpedances(previews.map(p => ({
      name: p.name, r: p.new.r, x: p.new.x, b: p.new.b,
    })))
    qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
  } catch (e) {
    toast.error(
      `Could not update line impedance (${previews.length} line${previews.length === 1 ? '' : 's'}) — values unchanged.`
    )
    throw e
  }
}

// Immaterial changes (rel_change <= RESCALE_PROMPT_THRESHOLD) are applied
// straight away; material ones queue in the shared store for the dialog.
// Blocked ones (skipped_reason !== null) are queued too — surfaced by the
// dialog as "not rescaled", never silently dropped and never auto-applied.
//
// A failed AUTO apply must not just vanish: it was never added to
// `pendingRescale` (that's the whole point of "immaterial — don't ask"), so
// if the write fails there is nothing else that will ever ask about it — the
// per-km value stays wrong with only a toast as the trace, and a toast can be
// missed or dismissed. Chosen fix: re-queue the failed batch into
// `pendingRescale` on rejection, turning a failed silent write into an
// explicit ask instead of a silently lost one. `applyRescale` already
// toasted the failure; this just recovers the data.
//
// Every write path that receives a `{ rescale: RescalePreview[] }` response
// (MapCanvas's drag + recalc, PropertiesPanel's Bus form, TopologyCanvas's
// BusEditor) must call this from its mutation's `onSuccess`. Skipping it is
// exactly the bug this module was extracted to close.
export function ingestRescale(qc: QueryClient, previews: RescalePreview[] | undefined): void {
  if (!previews || previews.length === 0) return
  const { auto, ask, blocked } = partitionRescale(previews)
  const { setPendingRescale } = useRescaleStore.getState()
  if (auto.length) {
    void applyRescale(qc, auto).catch(() => {
      setPendingRescale(prev => [...prev, ...auto])
    })
  }
  if (ask.length || blocked.length) setPendingRescale(prev => [...prev, ...ask, ...blocked])
}
