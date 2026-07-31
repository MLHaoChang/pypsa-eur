import { useQueryClient } from '@tanstack/react-query'
import RescaleDialog from './RescaleDialog'
import { useRescaleStore } from '../store/rescaleStore'
import { applyRescale } from '../utils/rescaleActions'

// Single, app-wide instance of the impedance-rescale consent dialog (D-B4).
// Rendered once from App.tsx — see store/rescaleStore.ts for why this moved
// out of MapCanvasInner: any of MapCanvas's drag/recalc handlers,
// PropertiesPanel's Bus form, or TopologyCanvas's BusEditor can feed a
// preview into the shared store via `ingestRescale`, and this is the one
// place that asks about it, regardless of which surface triggered the
// length change.
//
// Gated on `!placementActive`: MapCanvas's click-to-place mode still queues
// previews into the store while running (via `ingestRescale`), it just
// doesn't want a modal stealing focus mid-click — the batch surfaces the
// moment placement ends, however it ends (last bus placed, Escape, "Done",
// or MapCanvas unmounting). See rescaleStore's `placementActive` doc.
export default function RescaleDialogHost() {
  const qc = useQueryClient()
  const pendingRescale = useRescaleStore(s => s.pendingRescale)
  const placementActive = useRescaleStore(s => s.placementActive)
  const setPendingRescale = useRescaleStore(s => s.setPendingRescale)

  if (placementActive) return null

  return (
    <RescaleDialog
      previews={pendingRescale.filter(p => p.skipped_reason === null)}
      blocked={pendingRescale.filter(p => p.skipped_reason !== null)}
      onAccept={async () => {
        try {
          await applyRescale(qc, pendingRescale.filter(p => p.skipped_reason === null))
          setPendingRescale([])
        } catch {
          // applyRescale already toasted the failure. Leave pendingRescale
          // intact — the write didn't happen, nothing was lost, and the
          // dialog stays open with the same batch so the user can retry
          // Update or decline.
        }
      }}
      onDecline={() => setPendingRescale([])}
    />
  )
}
