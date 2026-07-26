import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { authApi, type AuthUser } from '../api/auth'
import { authEnabled } from './config'

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
  const [user, setUser] = useState<AuthUser | null>(null)
  const [status, setStatus] = useState<AuthStatus>(authEnabled ? 'loading' : 'authenticated')

  const refresh = useCallback(async (): Promise<AuthUser | null> => {
    if (!authEnabled) {
      setUser(null)
      setStatus('authenticated')
      return null
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
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(async (email: string, password: string): Promise<AuthUser> => {
    const response = await authApi.login({ email, password })
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
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    status,
    isAuthenticated: status === 'authenticated',
    isAdmin: Boolean(user?.is_super_admin),
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
