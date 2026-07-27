import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { authApi, type AuthUser } from '../api/auth'
import { hasAdminConsoleAccess } from '../pages/admin/helpers'
import { useAuthMode } from './AuthModeProvider'
import { localAdminUser } from './localMode'
import { loginWithPassword } from './requests'

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const { authEnabled, ready: authModeReady } = useAuthMode()
  const [user, setUser] = useState<AuthUser | null>(null)
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
  }, [authEnabled])

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
  }, [])

  const logout = useCallback(async (): Promise<void> => {
    try {
      if (authEnabled) {
        await authApi.logout()
      }
    } finally {
      setUser(null)
      setStatus(authEnabled ? 'unauthenticated' : 'authenticated')
    }
  }, [authEnabled])

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
