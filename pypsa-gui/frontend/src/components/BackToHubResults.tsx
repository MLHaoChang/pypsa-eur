// The way back from a panel Guided opened out of the hub design's Results step
// ("Open the detail", "Open the grid checks"): it returns to that step (UX
// template audit M10, owner decision D2). One component so every such panel
// says and does the same thing.
import { ArrowLeft } from 'lucide-react'
import { useHubDesignStore } from '../pages/hubDesign/hubDesignStore'
import { useUIStore } from '../store/uiStore'

export default function BackToHubResults() {
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  return (
    <button
      type="button"
      data-testid="results-back-to-hub"
      onClick={() => {
        useHubDesignStore.getState().setStep('results', { user: true })
        setSlidePanel('hubDesign')
      }}
      className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-text hover:border-accent hover:text-accent"
    >
      <ArrowLeft size={12} aria-hidden="true" />
      Back to Results
    </button>
  )
}
