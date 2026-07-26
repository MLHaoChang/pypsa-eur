// Auth is opt-in. A default clone runs as a single-user workbench without
// Postgres or auth. Set VITE_AUTH_ENABLED=true to enable multi-user auth.
export const authEnabled = import.meta.env.VITE_AUTH_ENABLED === 'true'
