// Issue 49: only a super-admin asks for the model settings; anyone else makes no request.
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fetchLLMSettingsOrNull } from '../api/llmSettings'
import { useLLMSettings, useLLMSettingsAvailable } from './useLLMSettings'

const asSuperAdmin = vi.hoisted(() => ({ value: true }))
vi.mock('../auth/AuthProvider', () => ({ useIsSuperAdmin: () => asSuperAdmin.value }))
vi.mock('../api/llmSettings', () => ({ fetchLLMSettingsOrNull: vi.fn(async () => ({ profiles: [] })) }))

const wrapper = ({ children }: { children: ReactNode }) =>
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>

describe('useLLMSettings', () => {
  it('a member makes no request and the section stays unavailable', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockClear()
    asSuperAdmin.value = false
    const { result } = renderHook(() => ({ q: useLLMSettings(), avail: useLLMSettingsAvailable() }), { wrapper })
    await new Promise((r) => setTimeout(r, 50))
    expect(fetchLLMSettingsOrNull).not.toHaveBeenCalled()
    expect(result.current.q.data).toBeUndefined()
    expect(result.current.q.isLoading).toBe(false)
    expect(result.current.avail).toBe(false)
  })

  it('a super-admin reads them', async () => {
    vi.mocked(fetchLLMSettingsOrNull).mockClear()
    asSuperAdmin.value = true
    const { result } = renderHook(() => useLLMSettings(), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ profiles: [] }))
    expect(fetchLLMSettingsOrNull).toHaveBeenCalledTimes(1)
  })
})
