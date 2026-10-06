import { Toaster } from 'react-hot-toast'
import { useLocation } from 'react-router-dom'
import { useUIStore } from '../store/uiStore'

/** react-hot-toast's own inset from the viewport edge. */
export const TOAST_GAP = 16

/** The routes whose page mounts `AssistantDock` (App.tsx, ProjectsHomePage). */
const DOCK_ROUTES = new Set(['/app', '/projects'])

/**
 * The app's one `<Toaster>` (bottom-right), moved out of main.tsx so it can
 * read the assistant dock (P31 C2).
 *
 * The dock (`AssistantDock`, its width `assistantDockWidth` in the store) is
 * a right-hand column — mounted by the workbench (/app) and by the projects
 * page (/projects) — with the composer and its Send button at the bottom. A
 * bottom-right toast therefore sat over Send: the "Created '<name>' from
 * template" toast most of all, raised on /projects as the new project opens.
 * While the dock is open on a page that mounts it, the toasts move left of
 * it; elsewhere (login, admin) they keep the corner.
 */
export default function ToasterHost() {
  const dockOpen = useUIStore(s => s.assistantDockOpen)
  const dockWidth = useUIStore(s => s.assistantDockWidth)
  const dockOnPage = DOCK_ROUTES.has(useLocation().pathname)
  const right = dockOpen && dockOnPage ? dockWidth + TOAST_GAP : TOAST_GAP
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
