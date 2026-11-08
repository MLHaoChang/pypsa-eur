// SC-07 J5: the scene GET carries the caller's own my_previews, so its cached
// copy must never show to the next user signed in in the same document.
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthProvider'
import { sceneKey } from '../site3d/sceneActions'
import { nk } from '../utils/queryKeys'

const me = vi.hoisted(() => ({ current: { id: 'u-a', email: 'a@example.com' } as { id: string; email: string } }))

vi.mock('./AuthModeProvider', () => ({ useAuthMode: () => ({ authEnabled: true, ready: true }) }))
vi.mock('../api/auth', () => ({
  authApi: { me: vi.fn(async () => me.current), logout: vi.fn(async () => ({ ok: true })) },
}))
vi.mock('./requests', () => ({
  loginWithPassword: vi.fn(async (email: string) => ({ user: { id: email === 'b@example.com' ? 'u-b' : 'u-a', email } })),
}))

let auth: ReturnType<typeof useAuth>
function Probe() {
  auth = useAuth()
  return null
}

function setup() {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>)
  return qc
}

const scene = sceneKey('p1', 's1')
const buses = nk('p1', 'buses')

describe('AuthProvider drops user-scoped queries when the user changes', () => {
  it('keeps the scene for the same user and drops it for another', async () => {
    const qc = setup()
    await waitFor(() => expect(auth.user?.id).toBe('u-a'))
    qc.setQueryData(scene, { my_previews: [{ id: 'mine' }] })
    qc.setQueryData(buses, ['B1'])

    await act(async () => { await auth.refresh() })
    expect(qc.getQueryData(scene)).toBeDefined()

    await act(async () => { await auth.login('b@example.com', 'pw') })
    expect(auth.user?.id).toBe('u-b')
    expect(qc.getQueryData(scene)).toBeUndefined()
    expect(qc.getQueryData(buses)).toEqual(['B1'])
  })

  it('drops the scene on sign-out', async () => {
    const qc = setup()
    await waitFor(() => expect(auth.user?.id).toBe('u-a'))
    qc.setQueryData(scene, { my_previews: [{ id: 'mine' }] })
    await act(async () => { await auth.logout() })
    expect(qc.getQueryData(scene)).toBeUndefined()
  })
})
