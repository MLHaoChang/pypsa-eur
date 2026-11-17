// template-e2e 02, owner decision (a): the connection limit is the import links' capacity. The pack form
// the Site card's readiness and the Goal card's study send takes the import cap from the network, so an
// edit to the limit (Guided or Expert) is what a weak-grid study runs. Mirrors
// `flow_service.with_network_import` (backend/tests/test_flow_connection_limit.py).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { buildEhStudyBody } from '../results/EhReferenceDesignPanel'
import { DC_TEMPLATE } from './testFixtures'
import { templateForm, useTemplateForm } from './useHubData'

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getLinks: vi.fn() } }
})

afterEach(() => { cleanup(); vi.clearAllMocks() })
function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useTemplateForm', () => {
  it('takes the weak-grid import cap from the import links, not the template', async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    vi.mocked(networkApi.getLinks).mockResolvedValue([
      { name: 'grid_import', eh_role: 'grid_import', p_nom: 45 }, { name: 'ups', eh_role: '', p_nom: 9 }] as never)
    const { result } = renderHook(() => useTemplateForm(DC_TEMPLATE), { wrapper: wrap })
    expect(templateForm(DC_TEMPLATE).importMw).toBe('40')
    await waitFor(() => expect(result.current.importMw).toBe('45'))
    const body = buildEhStudyBody('weak_flexible', result.current).body
    expect(body?.pack_overrides?.import_p_nom_mw).toBe(45)
  })

  it("keeps the template's value while the project has no import link", async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    vi.mocked(networkApi.getLinks).mockResolvedValue([{ name: 'ups', eh_role: '', p_nom: 9 }] as never)
    const { result } = renderHook(() => useTemplateForm(DC_TEMPLATE), { wrapper: wrap })
    await waitFor(() => expect(networkApi.getLinks).toHaveBeenCalled())
    expect(result.current.importMw).toBe('40')
  })
})
