/**
 * Post-logout navigation.
 *
 * Uses a full document load rather than react-router so the Vite/serve auth
 * gate can hand back the static sign-in document. A client-side navigation
 * would keep the already-booted SPA mounted and render the in-app auth route
 * instead, which is how signed-out users ended up on a different-looking login
 * screen than the one they signed in through.
 */
export const SIGNED_OUT_PATH = '/'

export function redirectAfterLogout(assign: (url: string) => void = url => window.location.assign(url)): void {
  assign(SIGNED_OUT_PATH)
}
