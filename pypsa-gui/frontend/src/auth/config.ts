// Multi-user auth UI is always on for this branch.
// Set VITE_AUTH_ENABLED=false only if you intentionally need the classic
// single-user workbench (not supported while backend auth is enabled).

const flag = import.meta.env.VITE_AUTH_ENABLED

export let authEnabled = flag !== 'false' && flag !== '0'

export function setAuthEnabled(next: boolean): void {
  authEnabled = next
}

export function getAuthEnabled(): boolean {
  return authEnabled
}
