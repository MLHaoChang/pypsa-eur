import { Toaster } from 'react-hot-toast'
import { useLocation } from 'react-router-dom'
import { useUIStore } from '../store/uiStore'

/** react-hot-toast's own inset from the viewport edge. */
export const TOAST_GAP = 16

/**
 * The app's one `<Toaster>` (bottom-right), moved out of main.tsx so it can
 * read the assistant dock (P31 C2).
 *
 * The dock is a right-hand column on the workbench (`AssistantDock`, its
 * width `assistantDockWidth` in the store) with the composer and its Send
 * button at the bottom. A bottom-right toast therefore sat over Send — the
 * "Created '<name>' from template" toast most of all, which fires as the new
 * project opens. While the dock is open on /app the toasts move left of it.
 * Off /app there is no dock on screen, so they keep the corner.
 */
export default function ToasterHost() {
  const dockOpen = useUIStore(s => s.assistantDockOpen)
  const dockWidth = useUIStore(s => s.assistantDockWidth)
  const onWorkbench = useLocation().pathname === '/app'
  const right = dockOpen && onWorkbench ? dockWidth + TOAST_GAP : TOAST_GAP
  return (
    <Toaster
      position="bottom-right"
      containerStyle={{ right }}
      toastOptions={{
        style: {
          fontSize: 13,
          background: 'var(--color-panel)',
          color: 'var(--color-text)',
          border: '1px solid var(--color-border)',
        },
      }}
    />
  )
}
