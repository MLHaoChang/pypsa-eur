// The auth context on its own, with no side-effect imports, so a page can
// read who is signed in without loading the provider's dependencies (07g:
// ReportViewer imports this, not AuthProvider, whose import of
// `improvePrefill` subscribes to the UI store at module load).
import { createContext, useContext } from 'react'
import type { AuthUser } from '../api/auth'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export interface AuthContextValue {
  user: AuthUser | null
  status: AuthStatus
  isAuthenticated: boolean
  isAdmin: boolean
  login: (email: string, password: string) => Promise<AuthUser>
  logout: () => Promise<void>
  refresh: () => Promise<AuthUser | null>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

/** The signed-in user's email, or null when signed out or outside an
 *  AuthProvider (07g: whose report comments are theirs). */
export function useAuthUserEmail(): string | null {
  return useContext(AuthContext)?.user?.email ?? null
}
