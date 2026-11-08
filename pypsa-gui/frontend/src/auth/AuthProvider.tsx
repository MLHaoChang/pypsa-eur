import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { authApi, type AuthUser } from '../api/auth'
import { hasAdminConsoleAccess } from '../pages/admin/helpers'
import { useAuthMode } from './AuthModeProvider'
import { localAdminUser } from './localMode'
import { loginWithPassword } from './requests'
import { USER_SCOPED_ROOTS } from '../utils/queryKeys'

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

interface AuthContextValue {
  user: AuthUser | null
  status: AuthStatus
  isAuthenticated: boolean
  isAdmin: boolean
  login: (email: string, password: string) => Promise<AuthUser>
  logout: () => Promise<void>
  refresh: () => Promise<AuthUser | null>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function isUnauthorized(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 401
}

/** Drops the cached data of the user who was signed in, so the next user never sees it (SC-07 J5). */
export function dropUserScopedQueries(qc: QueryClient): void {
  USER_SCOPED_ROOTS.forEach(root => qc.removeQueries({ queryKey: [root] }))
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const { authEnabled, ready: authModeReady } = useAuthMode()
  const qc = useQueryClient()
  const [user, setUserState] = useState<AuthUser | null>(null)
  // The id last signed in; undefined before the first answer. A change drops user-scoped queries before any render.
  const signedIn = useRef<string | null | undefined>(undefined)
  const setUser = useCallback((next: AuthUser | null) => {
    const id = next?.id ?? null
    if (signedIn.current !== undefined && signedIn.current !== id) dropUserScopedQueries(qc)
    signedIn.current = id
    setUserState(next)
  }, [qc])
  const [status, setStatus] = useState<AuthStatus>('loading')

  const refresh = useCallback(async (): Promise<AuthUser | null> => {
    if (!authEnabled) {
      // A synthetic admin, not null. `null` made hasAdminConsoleAccess(null)
      // false, so /admin/* bounced to /projects — but a local user owns their
      // machine and the admin console is where the settings live.
      const local = localAdminUser()
      setUser(local)
      setStatus('authenticated')
      return local
    }

    try {
      const nextUser = await authApi.me()
      setUser(nextUser)
      setStatus('authenticated')
      return nextUser
    } catch (error) {
      setUser(null)
      setStatus('unauthenticated')
      if (isUnauthorized(error)) return null
      return null
    }
  }, [authEnabled, setUser])

  useEffect(() => {
    if (!authModeReady) {
      setStatus('loading')
      return
    }
    void refresh()
  }, [authModeReady, refresh])

  const login = useCallback(async (email: string, password: string): Promise<AuthUser> => {
    const response = await loginWithPassword(email, password)
    setUser(response.user)
    setStatus('authenticated')
    return response.user
  }, [setUser])

  const logout = useCallback(async (): Promise<void> => {
    try {
      if (authEnabled) {
        await authApi.logout()
      }
    } finally {
      setUser(null)
      setStatus(authEnabled ? 'unauthenticated' : 'authenticated')
    }
  }, [authEnabled, setUser])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    status,
    isAuthenticated: status === 'authenticated',
    isAdmin: hasAdminConsoleAccess(user),
    login,
    logout,
    refresh,
  }), [login, logout, refresh, status, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
