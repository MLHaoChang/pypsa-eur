import { Compass } from 'lucide-react'

/**
 * The `hubDesign` panel slot (guided-mode spec §3.6). P23 ships the shell
 * only — the slot, its breadcrumb, the Guided nav entry and the auto-open
 * rule. The step rail and its five cards replace this body in P24.
 */
export default function HubDesignPanel() {
  return (
    <div
      data-testid="hub-design-panel"
      className="h-full flex flex-col items-center justify-center gap-3 p-8 text-center"
    >
      <Compass size={28} className="text-accent" />
      <p className="text-[14px] font-semibold text-text">Hub design</p>
      <p className="text-[12px] text-muted max-w-sm">
        Coming in the next step. Until then, ask the assistant — it can open any panel for you.
      </p>
    </div>
  )
}
