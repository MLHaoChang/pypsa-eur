import { ArrowLeft } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { returnToOrigin } from './flowNavigation'

/** "Back to <origin>": shown after a fix link jumped away, in Guided and in
 *  Expert alike (design §2.2). Mounted once; flowNavigation owns the state. */
export function FlowReturnButton() {
  const back = useUIStore(s => s.flowReturn)
  if (!back) return null
  return (
    <button type="button" data-testid="flow-return" data-no-panel-close onClick={returnToOrigin}
      className="fixed bottom-10 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-bg px-3 py-1.5 text-[12px] font-medium text-text shadow-lg hover:border-accent hover:text-accent">
      <ArrowLeft size={13} />Back to {back.label}
    </button>
  )
}
