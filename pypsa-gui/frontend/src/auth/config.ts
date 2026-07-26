// Auth is enabled in development via `frontend/.env.development`
// (`VITE_AUTH_ENABLED=true`). Override with `.env.local` if needed.
// Production / single-user: set VITE_AUTH_ENABLED=false (or leave unset in a
// build that does not load `.env.development`).
export const authEnabled = import.meta.env.VITE_AUTH_ENABLED === 'true'
