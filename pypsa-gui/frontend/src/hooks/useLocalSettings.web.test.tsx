import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setAuthEnabled } from '../auth/config'
import { fetchLocalSettings } from '../api/localSettings'
import { useLocalSettings } from './useLocalSettings'

vi.mock('../api/localSettings', () => ({ fetchLocalSettings: vi.fn().mockResolvedValue(null) }))

const wrap = ({ children }: { children: ReactNode }) =>
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>

beforeEach(() => vi.clearAllMocks())

describe('useLocalSettings (N15)', () => {
  it('does not ask a web build (auth on) for /local-settings', async () => {
    setAuthEnabled(true)
    renderHook(() => useLocalSettings(), { wrapper: wrap })
    await new Promise(r => setTimeout(r, 50))
    expect(fetchLocalSettings).not.toHaveBeenCalled()
  })
  it('asks the desktop build (auth off)', async () => {
    setAuthEnabled(false)
    renderHook(() => useLocalSettings(), { wrapper: wrap })
    await waitFor(() => expect(fetchLocalSettings).toHaveBeenCalled())
  })
})
