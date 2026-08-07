import { MessageSquare, PanelRightClose } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import ChatPanel from './ChatPanel'
import { ErrorBoundary } from './ErrorBoundary'

/**
 * The assistant's own column, mounted beside the main area rather than inside
 * the SlidePanel slot.
 *
 * Why this is not a SlidePanel: `activeSlidePanel` holds ONE value, so while
 * the assistant occupied it the assistant was mutually exclusive with every
 * view it exists to explain — and `applyUiNavigate` calling
 * `setSlidePanel('results')` closed the assistant in the act of obeying you.
 *
 * ChatPanel is rendered unconditionally and hidden with CSS when collapsed.
 * Unmounting it mid-turn is what produced "still streaming, no tokens on
 * screen"; keeping it mounted is the fix, not an optimisation.
 */
export default function AssistantDock() {
  const { assistantDockOpen, setAssistantDockOpen } = useUIStore()

  return (
    <div
      className={`flex flex-col min-h-0 border-l border-border bg-bg shrink-0 ${
        assistantDockOpen ? 'w-[380px]' : 'w-10'
      }`}
      data-testid="assistant-dock"
    >
      {assistantDockOpen ? (
        <div className="flex items-center gap-2 px-3 h-9 border-b border-border bg-bg-2 shrink-0">
          <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent">
            ASSISTANT
          </span>
          <span className="flex-1" />
          <button
            onClick={() => setAssistantDockOpen(false)}
            title="Collapse the assistant"
            data-testid="assistant-dock-collapse"
            className="text-muted hover:text-text p-1 rounded hover:bg-panel transition-colors"
          >
            <PanelRightClose size={14} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAssistantDockOpen(true)}
          title="Open the assistant"
          data-testid="assistant-dock-launcher"
          className="flex items-center justify-center h-10 w-full text-muted hover:text-accent hover:bg-panel transition-colors"
        >
          <MessageSquare size={16} />
        </button>
      )}

      {/* Never unmounted — see the module docstring. */}
      <div
        className={`flex-1 min-h-0 overflow-hidden ${assistantDockOpen ? '' : 'hidden'}`}
        data-testid="assistant-dock-body"
      >
        <ErrorBoundary label="The assistant crashed">
          <ChatPanel />
        </ErrorBoundary>
      </div>
    </div>
  )
}
