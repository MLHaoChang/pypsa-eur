// Multi-user auth UI.
//
// Default ON for this branch (login / projects / admin). Set
// `VITE_AUTH_ENABLED=false` in `.env.local` to force the classic single-user
// workbench. Backend auth is still gated by `PYPSA_GUI_AUTH_ENABLED`.
//
// `authEnabled` is a live binding — when the API reports auth is required we
// upgrade it at runtime so a stale Vite env / HMR session cannot leave the
// reviewer stuck on the workbench with "Authentication required" toasts.

const flag = import.meta.env.VITE_AUTH_ENABLED

export let authEnabled = flag !== 'false' && flag !== '0'

export function setAuthEnabled(next: boolean): void {
  authEnabled = next
}

export function getAuthEnabled(): boolean {
  return authEnabled
}
